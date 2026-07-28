"""
services/health_monitor.py — System Health Monitor

Outcome-based watchdog for every outreach-shaped agent in Duding, plus
a scheduler-wide staleness check that covers literally every
APScheduler job (including ones added after this file was written —
see outreach_engine.py::_job_listener, which is what makes new agents
"inherit" coverage without any code here needing to know about them).

Two kinds of checks:
  1. Per-agent outcome checks (AGENT_REGISTRY) — reply rate, bounce
     rate, held-for-review backlog, send-volume trend. Currently
     covers: outreach (OutreachProspect), brand_deals (BrandProspect).
     CHKD isn't in this registry on purpose — it doesn't run cold
     outreach with a reply/bounce funnel, so those checks don't apply
     to it. It still gets covered by (2) and by the Resend-error
     check, since CHKD's sends go through the same send_email().
  2. System-wide checks — job staleness (any scheduler job, any
     agent) and Resend API errors in the last 24h (any sender).

Baselines (HealthBaseline table) hold the expected range per
(agent, metric) so alerts compare against a real benchmark, not just
"is it exactly zero". Seeded once via seed_health_baselines().
"""
from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from db import SessionLocal
from models.health_baseline import HealthBaseline
from models.health_check_run import HealthCheckRun
from models.job_health import JobHealth
from models.email_send_error import EmailSendError
from models.outreach_prospect import OutreachProspect
from models.outreach_activity import OutreachActivity
from models.brand_prospect import BrandProspect
from models.brand_outreach_email import BrandOutreachEmail

CRITICAL = "critical"
WARNING = "warning"
HEALTHY = "healthy"

_SEVERITY_RANK = {HEALTHY: 0, WARNING: 1, CRITICAL: 2}

# ── Agent registry — the extensibility point for outcome checks ────────────
# To wire a NEW cold-outreach-shaped agent into these checks: add an
# entry here. Everything else (job staleness, Resend errors) already
# covers it automatically via the scheduler-wide listener + the
# shared send_email() choke point.

AGENT_REGISTRY: List[Dict[str, Any]] = [
    {
        "name": "outreach",
        "prospect_model": OutreachProspect,
        "replied_statuses": ["replied", "booked"],
        "held_for_review_statuses": ["pending_review"],
        "contacted_field": "last_contacted_at",
        "backlog_statuses": ["queued"],
        "activity_model": OutreachActivity,
        "activity_sent_types": ["email_sent", "follow_up_sent"],
        "activity_date_field": "created_at",
        "resend_api_key_env": "RESEND_API_KEY",
    },
    {
        "name": "brand_deals",
        "prospect_model": BrandProspect,
        "replied_statuses": ["replied", "negotiating", "deal_won", "deal_lost"],
        "held_for_review_statuses": ["held_for_review"],
        "contacted_field": "last_contacted_at",
        "backlog_statuses": ["verified", "queued"],
        "activity_model": BrandOutreachEmail,
        "activity_sent_types": None,  # every row is a send
        "activity_date_field": "sent_at",
        "resend_api_key_env": "CHKD_RESEND_API_KEY",
    },
]

# ── Baselines ────────────────────────────────────────────────────────────

_DEFAULT_BASELINES = [
    # agent, metric, min, max, unit, notes
    ("outreach",    "reply_rate_pct",         1.0, 5.0,  "pct", "Cold outreach industry benchmark"),
    ("outreach",    "bounce_rate_pct",        0.0, 5.0,  "pct", "Above 5% risks sender-reputation degradation"),
    ("outreach",    "held_for_review_pct",    0.0, 30.0, "pct", "Share of prospects with no usable contact found"),
    ("brand_deals", "reply_rate_pct",         1.0, 5.0,  "pct", "Cold outreach industry benchmark (same as trade outreach)"),
    ("brand_deals", "bounce_rate_pct",        0.0, 5.0,  "pct", "Above 5% risks sender-reputation degradation"),
    ("brand_deals", "held_for_review_pct",    0.0, 30.0, "pct", "Share of prospects behind a generic inbox pending manual approval"),
    ("system",      "resend_errors_24h",      0.0, 0.0,  "count", "Any Resend API error in a rolling 24h window is worth a look"),
]


def seed_health_baselines() -> int:
    """Idempotent per-row (agent, metric) — safe on every restart."""
    db = SessionLocal()
    inserted = 0
    try:
        existing = {(b.agent, b.metric) for b in db.query(HealthBaseline.agent, HealthBaseline.metric).all()}
        for agent, metric, lo, hi, unit, notes in _DEFAULT_BASELINES:
            if (agent, metric) in existing:
                continue
            db.add(HealthBaseline(agent=agent, metric=metric, expected_min=lo, expected_max=hi, unit=unit, notes=notes))
            inserted += 1
        db.commit()
        if inserted:
            print(f"[health_monitor] seeded {inserted} baseline(s)")
        return inserted
    except Exception as exc:
        db.rollback()
        print(f"[health_monitor] baseline seed error: {exc}")
        return 0
    finally:
        db.close()


def _get_baseline(db, agent: str, metric: str) -> Optional[HealthBaseline]:
    return db.query(HealthBaseline).filter(HealthBaseline.agent == agent, HealthBaseline.metric == metric).first()


# ── Flag helper ──────────────────────────────────────────────────────────

def _flag(severity: str, agent: str, metric: str, message: str, action: str,
          value: Any = None, expected: Any = None) -> Dict[str, Any]:
    return {
        "severity": severity, "agent": agent, "metric": metric,
        "message": message, "action": action, "value": value, "expected": expected,
    }


# ── Per-agent outcome checks ─────────────────────────────────────────────

def _check_reply_rate(db, cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    Model = cfg["prospect_model"]
    contacted = db.query(Model).filter(getattr(Model, cfg["contacted_field"]).isnot(None)).count()
    if contacted == 0:
        return []

    total_sends = (
        db.query(cfg["activity_model"])
        .filter(getattr(cfg["activity_model"], cfg["activity_date_field"]).isnot(None))
        .count()
    )
    if cfg["activity_sent_types"]:
        total_sends = (
            db.query(cfg["activity_model"])
            .filter(cfg["activity_model"].activity_type.in_(cfg["activity_sent_types"]))
            .count()
        )

    replied = db.query(Model).filter(Model.status.in_(cfg["replied_statuses"])).count()
    reply_rate = round(replied / contacted * 100, 2) if contacted else 0.0

    if total_sends < 50:
        return []  # not enough volume to conclude anything yet

    baseline = _get_baseline(db, cfg["name"], "reply_rate_pct")
    flags = []
    if replied == 0:
        flags.append(_flag(
            CRITICAL, cfg["name"], "reply_rate_pct",
            f"0% reply rate after {total_sends} sends ({contacted} prospects contacted) — nobody has replied at all.",
            "Check inbox/reply-detection is actually working (IMAP creds, spam folder on the sending side), then sample 5 sent emails by hand for content/deliverability issues.",
            value=0.0, expected=f"{baseline.expected_min}-{baseline.expected_max}%" if baseline else None,
        ))
    elif baseline and baseline.expected_min is not None and reply_rate < baseline.expected_min:
        flags.append(_flag(
            WARNING, cfg["name"], "reply_rate_pct",
            f"Reply rate {reply_rate}% is below the {baseline.expected_min}-{baseline.expected_max}% benchmark ({replied}/{contacted} contacted).",
            "Review recent subject lines/personalization — a slow decline usually means the pitch has gone stale.",
            value=reply_rate, expected=f"{baseline.expected_min}-{baseline.expected_max}%",
        ))
    return flags


def _check_held_for_review(db, cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    Model = cfg["prospect_model"]
    total = db.query(Model).count()
    if total == 0:
        return []
    held = db.query(Model).filter(Model.status.in_(cfg["held_for_review_statuses"])).count()
    pct = round(held / total * 100, 1)
    baseline_max = 30.0
    db_baseline = _get_baseline(db, cfg["name"], "held_for_review_pct")
    if db_baseline and db_baseline.expected_max is not None:
        baseline_max = db_baseline.expected_max
    if pct > baseline_max:
        return [_flag(
            WARNING, cfg["name"], "held_for_review_pct",
            f"{held}/{total} prospects ({pct}%) stuck with no usable contact — over the {baseline_max}% baseline.",
            "Either the contact-scraping pipeline needs a wider net (more page patterns) or this batch of sources just doesn't publish emails; sample a few by hand to tell which.",
            value=pct, expected=f"<{baseline_max}%",
        )]
    return []


def _tz_aware(dt: datetime) -> datetime:
    """SQLite hands back naive datetimes even for DateTime(timezone=True)
    columns; Postgres (production) hands back aware ones. Normalize to
    UTC-aware before any comparison so this behaves the same on both."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _check_send_trend(db, cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    Model = cfg["prospect_model"]
    Activity = cfg["activity_model"]
    date_field = getattr(Activity, cfg["activity_date_field"])

    now = datetime.now(timezone.utc)
    q = db.query(date_field)
    if cfg["activity_sent_types"]:
        q = q.filter(Activity.activity_type.in_(cfg["activity_sent_types"]))
    rows = [_tz_aware(r[0]) for r in q.all() if r[0] is not None]
    rows = [d for d in rows if d >= now - timedelta(days=14)]

    last7 = sum(1 for d in rows if d >= now - timedelta(days=7))
    prior7 = sum(1 for d in rows if now - timedelta(days=14) <= d < now - timedelta(days=7))

    backlog = db.query(Model).filter(Model.status.in_(cfg["backlog_statuses"])).count()

    if prior7 == 0:
        return []  # no baseline week to compare against yet
    avg_last7 = last7 / 7
    avg_prior7 = prior7 / 7
    declining = avg_last7 < avg_prior7 * 0.7
    piling_up = backlog >= 20

    if declining and piling_up:
        return [_flag(
            WARNING, cfg["name"], "send_trend",
            f"Sends/day dropped from ~{avg_prior7:.1f} to ~{avg_last7:.1f} (last 7d vs. prior 7d) while {backlog} prospects sit in the sendable backlog.",
            "Check the sender job is actually firing (see job staleness below) and that the daily send cap isn't being hit before the queue clears.",
            value=round(avg_last7, 1), expected=f">= {round(avg_prior7 * 0.7, 1)}",
        )]
    return []


def _get_resend_bounce_stats(api_key: str, max_pages: int = 20) -> Optional[Dict[str, Any]]:
    if not api_key:
        return None
    import httpx

    headers = {"Authorization": f"Bearer {api_key}"}
    total = 0
    bounced = 0
    params: Dict[str, str] = {"limit": "100"}
    for _ in range(max_pages):
        try:
            r = httpx.get("https://api.resend.com/emails", headers=headers, params=params, timeout=20)
        except Exception as exc:
            print(f"[health_monitor] Resend bounce-stats fetch error: {exc}")
            break
        if r.status_code != 200:
            break
        data = r.json()
        batch = data.get("data", [])
        for e in batch:
            total += 1
            if e.get("last_event") == "bounced":
                bounced += 1
        if not data.get("has_more") or not batch:
            break
        params = {"limit": "100", "after": batch[-1]["id"]}
    if total == 0:
        return None
    return {"total": total, "bounced": bounced, "bounce_rate_pct": round(bounced / total * 100, 1)}


def _check_bounce_rate(db, cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    # Resend's /emails API can only be scoped by which API key sent the
    # message, not by which internal agent triggered it — and
    # brand_deals shares CHKD_RESEND_API_KEY with CHKD's own
    # transactional emails (welcome/streak/re-engagement). Attributing
    # 100% of that shared account's traffic to whichever agent happens
    # to be in this loop is exactly the bug that produced a false
    # "brand_deals bounce rate" alert from CHKD's real send volume
    # before Brand Deals had ever sent anything. Guard: only pull
    # account-wide Resend stats once this agent has actual recorded
    # sends of its own — otherwise there's nothing to legitimately
    # attribute, so skip rather than guess.
    own_sends = db.query(cfg["activity_model"]).count()
    if own_sends == 0:
        return []

    api_key = os.getenv(cfg["resend_api_key_env"], "").strip()
    stats = _get_resend_bounce_stats(api_key)
    if not stats:
        return []
    baseline = _get_baseline(db, cfg["name"], "bounce_rate_pct")
    threshold = baseline.expected_max if baseline and baseline.expected_max is not None else 5.0
    if stats["bounce_rate_pct"] > threshold:
        return [_flag(
            CRITICAL, cfg["name"], "bounce_rate_pct",
            f"Bounce rate {stats['bounce_rate_pct']}% ({stats['bounced']}/{stats['total']} sends) — over the {threshold}% baseline.",
            "SPF/DKIM are worth a quick re-check, but a rate this high after passing auth usually means the contact-discovery step is finding dead addresses — tighten the email-quality filter.",
            value=stats["bounce_rate_pct"], expected=f"<{threshold}%",
        )]
    return []


# ── System-wide checks ────────────────────────────────────────────────────

def _check_resend_errors_24h(db) -> List[Dict[str, Any]]:
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    errors = db.query(EmailSendError).filter(EmailSendError.occurred_at >= since).all()
    if not errors:
        return []
    by_domain: Dict[str, int] = defaultdict(int)
    for e in errors:
        addr = (e.from_addr or "unknown")
        domain = addr.split("@")[-1].rstrip(">").strip() if "@" in addr else addr
        by_domain[domain] += 1
    detail = ", ".join(f"{n} from {d}" for d, n in sorted(by_domain.items(), key=lambda x: -x[1]))
    return [_flag(
        WARNING, "system", "resend_errors_24h",
        f"{len(errors)} Resend API error(s) in the last 24h ({detail}).",
        "Check the Resend dashboard for account-level issues (rate limits, suspended domain) — a burst of errors usually isn't individual bad addresses.",
        value=len(errors), expected="0",
    )]


def _estimate_job_interval(trigger) -> timedelta:
    """Max legitimate gap between fires — exact for IntervalTrigger,
    sampled for CronTrigger (correctly captures e.g. an overnight gap
    for a 9am-9pm-only job, not just its shortest daytime gap)."""
    if isinstance(trigger, IntervalTrigger):
        return trigger.interval
    if isinstance(trigger, CronTrigger):
        # 15 days guarantees >=2 occurrences of even a weekly job
        # regardless of what day "now" happens to be — a 9-day horizon
        # sometimes captured only 1 Monday depending on the sampling
        # day, silently falling through to the fallback below and
        # masking whether the 2-sample computation actually ran.
        now = datetime.now(timezone.utc)
        horizon = now + timedelta(days=15)
        times: List[datetime] = []
        t = trigger.get_next_fire_time(None, now)
        while t and len(times) < 60 and t < horizon:
            times.append(t)
            nxt = trigger.get_next_fire_time(t, t)
            if not nxt or nxt <= t:
                break
            t = nxt
        if len(times) < 2:
            return timedelta(days=14)  # can't determine a cycle — stay conservative
        gaps = [times[i + 1] - times[i] for i in range(len(times) - 1)]
        return max(gaps)
    return timedelta(days=1)


# Best-effort proxy for "last successful run", used only when JobHealth
# has no row yet for a job (e.g. the very first run, before the
# scheduler listener has recorded anything). Returns None if no
# reasonable proxy exists — that's reported as "no history" rather
# than guessed at.
def _historical_proxy(db, job_id: str) -> Optional[datetime]:
    from sqlalchemy import func as _func
    try:
        if job_id == "find_prospects":
            return db.query(_func.max(OutreachProspect.created_at)).scalar()
        if job_id == "send_queued":
            return db.query(_func.max(OutreachActivity.created_at)).filter(
                OutreachActivity.activity_type == "email_sent"
            ).scalar()
        if job_id == "process_followups":
            return db.query(_func.max(OutreachActivity.created_at)).filter(
                OutreachActivity.activity_type == "follow_up_sent"
            ).scalar()
        if job_id == "brand_prospector":
            return db.query(_func.max(BrandProspect.found_at)).scalar()
        if job_id == "brand_pitch_sender":
            return db.query(_func.max(BrandOutreachEmail.sent_at)).scalar()
        if job_id == "social_intelligence":
            from models.social_intelligence_report import SocialIntelligenceReport
            return db.query(_func.max(SocialIntelligenceReport.created_at)).scalar()
        if job_id == "content_waitlist_sync":
            from models.waitlist_attribution import WaitlistAttribution
            return db.query(_func.max(WaitlistAttribution.created_at)).scalar()
        if job_id == "chkd_daily":
            from models.chkd_email import ChkdEmail
            return db.query(_func.max(ChkdEmail.sent_at)).filter(
                ChkdEmail.email_type.in_(["day3_reengagement", "day7_streak"])
            ).scalar()
    except Exception as exc:
        print(f"[health_monitor] historical proxy error for {job_id}: {exc}")
    return None


def _find_stale_jobs(db, scheduler) -> List[Dict[str, Any]]:
    """Shared detection core behind check_job_staleness (below) and the
    self-healing job-restart watch (services/self_healing.py) — one
    place decides what "stale" means, so the two never drift apart."""
    if not scheduler or not scheduler.running:
        return []
    stale: List[Dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    for job in scheduler.get_jobs():
        interval = _estimate_job_interval(job.trigger)
        threshold = interval * 2

        health = db.query(JobHealth).filter(JobHealth.job_id == job.id).first()
        last_success = health.last_success_at if health else None
        source = "recorded"
        if last_success is None:
            last_success = _historical_proxy(db, job.id)
            source = "proxy" if last_success else None

        if last_success is None:
            continue  # no signal either way — don't guess, don't false-flag

        if last_success.tzinfo is None:
            last_success = last_success.replace(tzinfo=timezone.utc)
        gap = now - last_success
        if gap > threshold:
            stale.append({"job_id": job.id, "gap": gap, "threshold": threshold, "source": source})
    return stale


def check_job_staleness(db, scheduler) -> List[Dict[str, Any]]:
    flags: List[Dict[str, Any]] = []
    for item in _find_stale_jobs(db, scheduler):
        gap, threshold = item["gap"], item["threshold"]
        flags.append(_flag(
            CRITICAL, "system", "job_staleness",
            f"'{item['job_id']}' hasn't run successfully in {gap.days}d {gap.seconds // 3600}h "
            f"(expected within ~{threshold}, {item['source']} signal).",
            "Check Railway logs for this job's id — likely an unhandled exception, a removed job, or the scheduler process restarted without recovering.",
            value=str(gap), expected=f"<{threshold}",
        ))
    return flags


# ── Orchestration ─────────────────────────────────────────────────────────

def run_health_check(scheduler=None) -> Dict[str, Any]:
    seed_health_baselines()
    db = SessionLocal()
    flags: List[Dict[str, Any]] = []
    raw_metrics: Dict[str, Any] = {}
    try:
        for cfg in AGENT_REGISTRY:
            agent_flags = []
            agent_flags += _check_reply_rate(db, cfg)
            agent_flags += _check_held_for_review(db, cfg)
            agent_flags += _check_send_trend(db, cfg)
            agent_flags += _check_bounce_rate(db, cfg)
            flags += agent_flags
            raw_metrics[cfg["name"]] = {"flag_count": len(agent_flags)}

        flags += _check_resend_errors_24h(db)
        flags += check_job_staleness(db, scheduler)

        overall = HEALTHY
        for f in flags:
            if _SEVERITY_RANK[f["severity"]] > _SEVERITY_RANK[overall]:
                overall = f["severity"]

        flags.sort(key=lambda f: -_SEVERITY_RANK[f["severity"]])

        run = HealthCheckRun(overall_status=overall, flags=flags, raw_metrics=raw_metrics)
        db.add(run)
        db.commit()
        db.refresh(run)

        return {"id": run.id, "run_at": run.run_at, "overall_status": overall, "flags": flags}
    finally:
        db.close()


def get_latest_health_check(db=None) -> Optional[Dict[str, Any]]:
    owns_db = db is None
    db = db or SessionLocal()
    try:
        run = db.query(HealthCheckRun).order_by(HealthCheckRun.run_at.desc()).first()
        if not run:
            return None
        return {"id": run.id, "run_at": run.run_at, "overall_status": run.overall_status, "flags": run.flags}
    finally:
        if owns_db:
            db.close()
