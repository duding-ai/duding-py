"""
services/self_healing.py — Phase 1 automated responses.

Distinct from services/health_monitor.py (detects and flags, never
acts) and services/agent_tasks.py (durable human-gated checklist,
never flips its own status): this module is the "act" half — it takes
safe, reversible, cheap-to-get-wrong actions on its own, logs every one
to action_log, and files an AgentTask instead of guessing whenever a
human decision is actually required.

Tier 1 only, per the self-healing spec (2026-07-28):
  1. Outreach bounce-rate rolling-window circuit breaker -> DB kill
     switch (never auto-clears — resume is a human decision).
  2. Scheduler job staleness -> one auto-restart attempt; if the job is
     still stale on a later sweep, escalate instead of retrying forever.

Run every 15 min by job_self_healing_sweep (outreach_engine.py), far
tighter than the 7:55am daily health check, since both failure modes
are cheap to detect quickly and expensive to leave for hours.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from db import SessionLocal
from models.outreach_activity import OutreachActivity
from models.outreach_sending_state import OutreachSendingState
from services.action_log import log_action, count_actions_since
from services.agent_tasks import file_task_if_new
from services.health_monitor import _find_stale_jobs

# ── Tier 1a — outreach bounce-rate circuit breaker ──────────────────────────

BOUNCE_WINDOW = 50
BOUNCE_AUTOPAUSE_THRESHOLD_PCT = float(os.getenv("OUTREACH_BOUNCE_AUTOPAUSE_THRESHOLD_PCT", "15.0"))


def _get_rolling_bounce_stats(api_key: str, window: int = BOUNCE_WINDOW) -> Optional[Dict[str, Any]]:
    """Most recent `window` sends on the account (Resend's /emails list is
    newest-first by default) — a rolling-window read, not the all-time
    average health_monitor._check_bounce_rate uses for its daily baseline
    comparison. Different question: "is right now on fire" vs. "is the
    long-run trend within benchmark"."""
    if not api_key:
        return None
    import httpx
    try:
        r = httpx.get(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}"},
            params={"limit": str(window)},
            timeout=20,
        )
    except Exception as exc:
        print(f"[self_healing] rolling bounce-stats fetch error: {exc}")
        return None
    if r.status_code != 200:
        return None
    batch = r.json().get("data", [])
    if not batch:
        return None
    total = len(batch)
    bounced = sum(1 for e in batch if e.get("last_event") == "bounced")
    return {
        "total": total,
        "bounced": bounced,
        "bounce_rate_pct": round(bounced / total * 100, 1),
        "sample_ids": [e.get("id") for e in batch if e.get("id")],
    }


def is_outreach_auto_paused(db=None) -> bool:
    owns = db is None
    db = db or SessionLocal()
    try:
        state = db.query(OutreachSendingState).filter(OutreachSendingState.id == 1).first()
        return bool(state and state.auto_paused_at)
    finally:
        if owns:
            db.close()


def clear_outreach_auto_pause() -> bool:
    """Human-only action (POST /dashboard/outreach/engine/clear-auto-pause)
    — the sweep that trips the kill switch never calls this itself."""
    db = SessionLocal()
    try:
        state = db.query(OutreachSendingState).filter(OutreachSendingState.id == 1).first()
        if not state or not state.auto_paused_at:
            return False
        reason = state.auto_pause_reason
        state.auto_paused_at = None
        state.auto_pause_reason = None
        db.commit()
        log_action(
            category="auto_pause_cleared", severity="info", agent="outreach",
            trigger=f"Human cleared auto-pause (was: {reason})",
            action_taken="Outreach kill switch cleared — sending resumes once OUTREACH_SENDING_ENABLED=true.",
        )
        return True
    finally:
        db.close()


def _trip_outreach_kill_switch(reason: str, sample: Dict[str, Any]) -> None:
    db = SessionLocal()
    try:
        state = db.query(OutreachSendingState).filter(OutreachSendingState.id == 1).first()
        if not state:
            state = OutreachSendingState(id=1)
            db.add(state)
        state.auto_paused_at = datetime.now(timezone.utc)
        state.auto_pause_reason = reason
        db.commit()
    finally:
        db.close()

    log_action(
        category="auto_pause", severity="critical", agent="outreach",
        trigger=reason,
        action_taken=(
            "Outreach sending kill switch tripped automatically — "
            "OUTREACH_SENDING_ENABLED is treated as false regardless of the env var "
            "until a human clears it via POST /dashboard/outreach/engine/clear-auto-pause."
        ),
        details=sample,
    )
    file_task_if_new(
        title="Diagnose outreach bounce-rate auto-pause",
        notes=(
            f"Auto-paused {datetime.now(timezone.utc).isoformat()}. {reason}\n\n"
            "This is a separate kill switch from the post-rebuild re-enable gate "
            "(REENABLE_TASK_TITLE) — it's the automated circuit breaker, not the "
            "one-time ramp checklist. Clear it from the dashboard once the cause is "
            "understood; the sampled Resend message ids are on the action_log row "
            "for this event."
        ),
        criteria=[
            {"description": "Root cause identified (contact-discovery quality, list decay, deliverability/DNS config)", "met": None, "detail": ""},
            {"description": "Fix deployed or confirmed not needed before clearing", "met": None, "detail": ""},
        ],
    )


def run_bounce_rate_watch() -> None:
    if is_outreach_auto_paused():
        return  # already tripped — don't re-log every sweep, resume is a human call

    db = SessionLocal()
    try:
        own_sends = db.query(OutreachActivity).filter(
            OutreachActivity.activity_type.in_(["email_sent", "follow_up_sent"])
        ).count()
    finally:
        db.close()
    if own_sends == 0:
        return  # nothing to legitimately attribute yet

    api_key = os.getenv("RESEND_API_KEY", "").strip()
    stats = _get_rolling_bounce_stats(api_key)
    if not stats or stats["total"] < BOUNCE_WINDOW:
        return  # not enough recent volume to trust a rolling-window read

    if stats["bounce_rate_pct"] > BOUNCE_AUTOPAUSE_THRESHOLD_PCT:
        reason = (
            f"Bounce rate {stats['bounce_rate_pct']}% over the last {stats['total']} sends "
            f"({stats['bounced']} bounced) — over the {BOUNCE_AUTOPAUSE_THRESHOLD_PCT}% auto-pause threshold."
        )
        _trip_outreach_kill_switch(reason, stats)


# ── Tier 1b — scheduled-job auto-restart ────────────────────────────────────

RESTART_LOOKBACK = timedelta(hours=24)


def run_job_staleness_watch(scheduler) -> None:
    if not scheduler or not scheduler.running:
        return
    db = SessionLocal()
    try:
        stale = _find_stale_jobs(db, scheduler)
    finally:
        db.close()

    since = datetime.now(timezone.utc) - RESTART_LOOKBACK
    for item in stale:
        job_id, gap, threshold = item["job_id"], item["gap"], item["threshold"]
        prior_restarts = count_actions_since("job_restart", since, job_id=job_id)

        if prior_restarts == 0:
            try:
                scheduler.modify_job(job_id, next_run_time=datetime.now(timezone.utc))
                log_action(
                    category="job_restart", severity="warning", job_id=job_id,
                    trigger=f"'{job_id}' stale — {gap} since last success (threshold {threshold}).",
                    action_taken="Forced immediate re-run via scheduler.modify_job(next_run_time=now).",
                )
            except Exception as exc:
                log_action(
                    category="job_restart", severity="critical", job_id=job_id,
                    trigger=f"'{job_id}' stale — {gap} since last success (threshold {threshold}).",
                    action_taken=f"Auto-restart attempt itself failed: {exc}",
                )
        else:
            log_action(
                category="escalation", severity="critical", job_id=job_id,
                trigger=(
                    f"'{job_id}' still stale ({gap} since last success, expected within {threshold}) "
                    f"after a prior auto-restart within the last 24h."
                ),
                action_taken="No further auto-restart attempted — filed for human review.",
            )
            file_task_if_new(
                title=f"Job '{job_id}' still stale after auto-restart",
                notes=(
                    f"An auto-restart was already attempted for '{job_id}' within the last 24h and "
                    f"it is still stale ({gap} since last success, expected within {threshold}). "
                    "Check Railway logs for this job id — likely an unhandled exception on every run, "
                    "not just a one-off hiccup."
                ),
            )


# ── Orchestration ────────────────────────────────────────────────────────────

def run_self_healing_sweep(scheduler=None) -> None:
    try:
        run_bounce_rate_watch()
    except Exception as exc:
        log_action(
            category="self_healing_error", severity="warning",
            trigger="run_bounce_rate_watch raised", action_taken=str(exc),
        )
    try:
        run_job_staleness_watch(scheduler)
    except Exception as exc:
        log_action(
            category="self_healing_error", severity="warning",
            trigger="run_job_staleness_watch raised", action_taken=str(exc),
        )
