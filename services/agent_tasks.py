"""
services/agent_tasks.py — Agent Task Queue

Durable, DB-backed checklist for work gated on a human decision at a
specific point in time — distinct from a scheduler job (which just
runs) and a dashboard action (which needs someone to click something
right now). An AgentTask sits at status='human_gated' with a due date
and a criteria checklist; check_reenable_criteria() evaluates the
criteria against real data on demand, it never auto-flips the status.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List

from db import SessionLocal
from models.agent_task import AgentTask
from models.outreach_prospect import OutreachProspect
from models.outreach_sending_state import OutreachSendingState

REENABLE_TASK_TITLE = "Re-enable outreach sending (post-rest-period gate)"
REENABLE_DUE_DATE = date(2026, 8, 4)


def seed_reenable_task() -> int:
    """Idempotent — inserts the 2026-08-04 re-enable checklist task
    once. Safe to call on every startup."""
    db = SessionLocal()
    try:
        existing = db.query(AgentTask).filter(AgentTask.title == REENABLE_TASK_TITLE).first()
        if existing:
            return 0
        db.add(AgentTask(
            title=REENABLE_TASK_TITLE,
            status="human_gated",
            due_date=REENABLE_DUE_DATE,
            criteria=[
                {"description": "Bounce rate <5% for 3 consecutive days", "met": None, "detail": ""},
                {"description": "Warmup ramp confirmed (rest period intact, ramp not prematurely started)", "met": None, "detail": ""},
                {"description": "Named-person-only filter confirmed active (no generic-quality prospects in the sendable queue)", "met": None, "detail": ""},
            ],
            notes=(
                "Filed 2026-07-22 following the contact-discovery rebuild and the 2-week rest "
                "period recommended by the domain-health verdict. Re-enabling OUTREACH_SENDING_ENABLED "
                "requires all 3 criteria met AND a human decision — this task never flips its own status."
            ),
        ))
        db.commit()
        print(f"[agent_tasks] seeded re-enable checklist task, due {REENABLE_DUE_DATE}")
        return 1
    except Exception as exc:
        db.rollback()
        print(f"[agent_tasks] seed error: {exc}")
        return 0
    finally:
        db.close()


def _check_bounce_rate_3_days() -> Dict[str, Any]:
    key = os.getenv("RESEND_API_KEY", "").strip()
    if not key:
        return {"met": None, "detail": "RESEND_API_KEY not set — cannot check"}

    import httpx
    headers = {"Authorization": f"Bearer {key}"}
    all_emails: List[Dict[str, Any]] = []
    params = {"limit": "100"}
    for _ in range(30):
        r = httpx.get("https://api.resend.com/emails", headers=headers, params=params, timeout=20)
        if r.status_code != 200:
            break
        data = r.json()
        batch = data.get("data", [])
        all_emails.extend(batch)
        if not data.get("has_more") or not batch:
            break
        params = {"limit": "100", "after": batch[-1]["id"]}

    today = datetime.now(timezone.utc).date()
    by_day: Dict[date, Dict[str, int]] = {}
    for e in all_emails:
        created = (e.get("created_at") or "")[:10]
        if not created:
            continue
        try:
            d = datetime.strptime(created, "%Y-%m-%d").date()
        except ValueError:
            continue
        if d >= today:
            continue  # only complete days count
        by_day.setdefault(d, {"total": 0, "bounced": 0})
        by_day[d]["total"] += 1
        if e.get("last_event") == "bounced":
            by_day[d]["bounced"] += 1

    last_3_days = sorted(by_day.keys(), reverse=True)[:3]
    if len(last_3_days) < 3:
        return {"met": False, "detail": f"only {len(last_3_days)} complete day(s) of send data available — need 3"}

    rates = []
    for d in last_3_days:
        stats = by_day[d]
        rate = (stats["bounced"] / stats["total"] * 100) if stats["total"] else 0.0
        rates.append((d, stats["total"], rate))

    all_under_5 = all(rate < 5.0 for _, total, rate in rates if total > 0)
    detail = ", ".join(f"{d}: {rate:.1f}% ({total} sends)" for d, total, rate in rates)
    return {"met": all_under_5, "detail": detail}


def _check_ramp_intact() -> Dict[str, Any]:
    enabled = os.getenv("OUTREACH_SENDING_ENABLED", "false").strip().lower() == "true"
    db = SessionLocal()
    try:
        state = db.query(OutreachSendingState).filter(OutreachSendingState.id == 1).first()
        enabled_since = state.enabled_since if state else None
    finally:
        db.close()

    if enabled:
        return {"met": False, "detail": "OUTREACH_SENDING_ENABLED is already true — rest period is not intact, this criterion is about confirming the ramp mechanism BEFORE re-enabling"}
    if enabled_since is not None:
        return {"met": False, "detail": f"ramp already shows enabled_since={enabled_since} despite sending being off — inconsistent state, investigate before re-enabling"}
    return {"met": True, "detail": "sending is off, ramp has not started — will begin at 10/day the moment OUTREACH_SENDING_ENABLED=true is set"}


def purge_generic_from_sendable_queue() -> int:
    """
    Retroactively applies the hard-exclusion rule to prospects already
    sitting in a sendable status from BEFORE the targeting rebuild
    shipped. Necessary because the exclusion logic lives inside
    job_send_next_queued/job_process_followups, which don't execute at
    all while OUTREACH_SENDING_ENABLED=false — meaning the "no generic
    address in the sendable queue" re-enable criterion could never
    become true before re-enabling under the original design (nothing
    would ever touch the leaked rows to reclassify them). Safe to run
    any time, including while sending is off — it never sends
    anything, only reclassifies status/confidence fields.
    """
    db = SessionLocal()
    try:
        leaked = (
            db.query(OutreachProspect)
            .filter(
                OutreachProspect.status.in_(["queued", "outreach_pending", "follow_up_pending"]),
                OutreachProspect.email_quality == "generic",
            )
            .all()
        )
        for p in leaked:
            p.status = "excluded_generic_address"
            p.confidence_tier = "none"
            p.confidence_score = 0
            p.confidence_reasons = ["excluded: generic role address — structurally excluded from cold outreach per the 2026-07-22 targeting rebuild"]
            p.email_note = "generic role address — hard-excluded from cold outreach targeting (retroactive purge)"
            db.add(p)
        db.commit()
        n = len(leaked)
        print(f"[agent_tasks] purged {n} generic-quality prospect(s) from the sendable queue")
        return n
    except Exception as exc:
        db.rollback()
        print(f"[agent_tasks] purge error: {exc}")
        return 0
    finally:
        db.close()


def _check_named_person_filter_active() -> Dict[str, Any]:
    db = SessionLocal()
    try:
        leaked = (
            db.query(OutreachProspect)
            .filter(
                OutreachProspect.status.in_(["queued", "outreach_pending", "follow_up_pending"]),
                OutreachProspect.email_quality == "generic",
            )
            .count()
        )
    finally:
        db.close()
    if leaked > 0:
        return {"met": False, "detail": f"{leaked} generic-quality prospect(s) still sitting in a sendable status — hard exclusion hasn't caught up to them yet (it applies at next scrape/send-time, not retroactively)"}
    return {"met": True, "detail": "zero generic-quality prospects in a sendable status"}


def check_reenable_criteria(persist: bool = True) -> Dict[str, Any]:
    """Evaluates all 3 re-enable criteria against real data right now.
    Never changes AgentTask.status itself — that's a human call."""
    results = {
        "Bounce rate <5% for 3 consecutive days": _check_bounce_rate_3_days(),
        "Warmup ramp confirmed (rest period intact, ramp not prematurely started)": _check_ramp_intact(),
        "Named-person-only filter confirmed active (no generic-quality prospects in the sendable queue)": _check_named_person_filter_active(),
    }
    all_met = all(r.get("met") is True for r in results.values())

    if persist:
        db = SessionLocal()
        try:
            task = db.query(AgentTask).filter(AgentTask.title == REENABLE_TASK_TITLE).first()
            if task:
                task.criteria = [
                    {"description": desc, "met": r.get("met"), "detail": r.get("detail", "")}
                    for desc, r in results.items()
                ]
                db.commit()
        except Exception as exc:
            db.rollback()
            print(f"[agent_tasks] persist criteria error: {exc}")
        finally:
            db.close()

    return {"all_met": all_met, "criteria": results, "checked_at": datetime.now(timezone.utc)}
