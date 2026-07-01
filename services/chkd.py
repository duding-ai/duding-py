"""
services/chkd.py — CHKD client email system

Queries CHKD's Supabase project via REST API for user + activity data.
Sends via Resend (services.email). Deduplicates against chkd_emails table
in Duding's Postgres so each email type fires at most once per cooldown window.

Required env vars:
  SUPABASE_URL         — e.g. https://vmpoexkcdcsbufqxwdwe.supabase.co
  SUPABASE_SERVICE_KEY — Supabase service-role key (Settings → API)
"""
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

from services.email import send_email
from models.chkd_email import ChkdEmail

# ── Supabase config ───────────────────────────────────────────────────────────

SUPABASE_URL = "https://vmpoexkcdcsbufqxwdwe.supabase.co"
CHKD_APP_URL = "https://getchkd.app"


def _supabase_key() -> str:
    return os.getenv("SUPABASE_SERVICE_KEY", "")

# Column names — change here if the schema uses different names
_PROFILES_TABLE   = "profiles"
_PROFILES_ID      = "id"
_PROFILES_EMAIL   = "email"
_PROFILES_NAME    = "full_name"   # try "name" if this doesn't exist
_PROFILES_CREATED = "created_at"

_DAYS_TABLE   = "days"
_DAYS_USER_ID = "user_id"
_DAYS_SCORE   = "score"
_DAYS_DATE    = "date"

# How many days to look back when querying the days table
_DAYS_LOOKBACK = 8

# ── Supabase REST helper ──────────────────────────────────────────────────────

def _sb_headers() -> Dict[str, str]:
    key = _supabase_key()
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def _sb_get(table: str, params: Optional[Dict] = None) -> List[Dict]:
    if not _supabase_key():
        print("[chkd] SUPABASE_SERVICE_KEY not set — skipping Supabase query")
        return []
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    try:
        r = httpx.get(url, headers=_sb_headers(), params=params or {}, timeout=20)
        if r.status_code == 200:
            return r.json() or []
        print(f"[chkd] Supabase {r.status_code}: {r.text[:200]}")
        return []
    except Exception as exc:
        print(f"[chkd] Supabase request failed: {exc}")
        return []


# ── Supabase data fetchers ────────────────────────────────────────────────────

def get_all_profiles() -> List[Dict[str, Any]]:
    return _sb_get(_PROFILES_TABLE, {
        "select": f"{_PROFILES_ID},{_PROFILES_EMAIL},{_PROFILES_NAME},{_PROFILES_CREATED}",
        "limit": "2000",
    })


def get_recent_days(lookback: int = _DAYS_LOOKBACK) -> List[Dict[str, Any]]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback)).date().isoformat()
    return _sb_get(_DAYS_TABLE, {
        "select": f"{_DAYS_USER_ID},{_DAYS_SCORE},{_DAYS_DATE}",
        f"{_DAYS_DATE}": f"gte.{cutoff}",
        "limit": "10000",
    })


def get_chkd_stats() -> Dict[str, Any]:
    """Stats for the admin dashboard. Returns counts + top lists."""
    profiles    = get_all_profiles()
    recent_days = get_recent_days(_DAYS_LOOKBACK)

    days_by_user: Dict[str, List[Dict]] = {}
    for row in recent_days:
        uid = row.get(_DAYS_USER_ID)
        if uid:
            days_by_user.setdefault(uid, []).append(row)

    at_risk = []
    streaks = []

    for p in profiles:
        uid       = p.get(_PROFILES_ID, "")
        email     = p.get(_PROFILES_EMAIL, "")
        name      = p.get(_PROFILES_NAME) or email
        created   = p.get(_PROFILES_CREATED, "")
        user_days = days_by_user.get(uid, [])

        if _has_streak(user_days, 7):
            streaks.append({"uid": uid, "name": name, "email": email})
        if _needs_reengagement(user_days, created):
            at_risk.append({"uid": uid, "name": name, "email": email})

    return {
        "total":          len(profiles),
        "streak7_count":  len(streaks),
        "at_risk_count":  len(at_risk),
        "streaks":        streaks[:25],
        "at_risk":        at_risk[:25],
    }


# ── Streak / re-engagement helpers ───────────────────────────────────────────

def _parse_date(val: Any) -> Optional[date]:
    if not val:
        return None
    try:
        return date.fromisoformat(str(val)[:10])
    except ValueError:
        return None


def _has_streak(user_days: List[Dict], length: int = 7) -> bool:
    """True if user scored > 0 on each of the last `length` calendar days (UTC)."""
    today  = datetime.now(timezone.utc).date()
    scored = {
        _parse_date(r.get(_DAYS_DATE))
        for r in user_days
        if (r.get(_DAYS_SCORE) or 0) > 0
    }
    scored.discard(None)
    return all((today - timedelta(days=i)) in scored for i in range(length))


def _needs_reengagement(user_days: List[Dict], created_at: Any) -> bool:
    """True if user signed up >3 days ago and has no entry in the last 3 days."""
    now    = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=3)

    if created_at:
        try:
            signup = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
            if signup.tzinfo is None:
                signup = signup.replace(tzinfo=timezone.utc)
            if signup >= cutoff:
                return False   # too new — give them time
        except ValueError:
            pass

    today        = now.date()
    logged_dates = {_parse_date(r.get(_DAYS_DATE)) for r in user_days}
    logged_dates.discard(None)
    return not any((today - timedelta(days=i)) in logged_dates for i in range(3))


# ── Email builders ────────────────────────────────────────────────────────────

def build_welcome_email(name: str) -> Tuple[str, str]:
    first   = (name or "there").split()[0]
    subject = f"Welcome to CHKD, {first}"
    body    = (
        f"Hey {first},\n\n"
        f"You just joined CHKD. That means you've committed to checking in with yourself every day "
        f"— and I'm glad you're here.\n\n"
        f"Here's how it works: every day you log your 5 non-negotiables. "
        f"Not goals. Not aspirations. The five things that, when you do them, make everything else better. "
        f"You track them, you score them, and over time the numbers tell the truth.\n\n"
        f"The goal isn't a perfect score. The goal is a real one.\n\n"
        f"Log your first check-in today: {CHKD_APP_URL}\n\n"
        f"Tommy"
    )
    return subject, body


def build_reengagement_email(name: str) -> Tuple[str, str]:
    first   = (name or "there").split()[0]
    subject = "You good?"
    body    = (
        f"Hey {first},\n\n"
        f"Haven't seen you check in on CHKD in a few days.\n\n"
        f"No judgment — life moves fast. But the whole point of the 5 non-negotiables "
        f"is that they work when you're consistent, not just when it's convenient.\n\n"
        f"Takes 30 seconds. Log today: {CHKD_APP_URL}\n\n"
        f"Tommy"
    )
    return subject, body


def build_streak_email(name: str, streak: int = 7) -> Tuple[str, str]:
    first   = (name or "there").split()[0]
    subject = f"{streak} days straight — that's real"
    body    = (
        f"Hey {first},\n\n"
        f"{streak} days in a row.\n\n"
        f"That's not motivation — that's discipline. Most people don't make it a week. You did.\n\n"
        f"Keep going. And if you know someone who needs this, send them the link: {CHKD_APP_URL}\n\n"
        f"One invite. Costs you nothing. Could actually help them.\n\n"
        f"Tommy"
    )
    return subject, body


# ── Dedup + send ──────────────────────────────────────────────────────────────

# Max time between repeat sends; None = only send once ever
_COOLDOWN_DAYS: Dict[str, Optional[int]] = {
    "welcome":           None,
    "day3_reengagement": 7,
    "day7_streak":       30,
}


def _already_sent(db, user_id: str, email_type: str) -> bool:
    cooldown = _COOLDOWN_DAYS.get(email_type)
    q = db.query(ChkdEmail).filter(
        ChkdEmail.user_id    == user_id,
        ChkdEmail.email_type == email_type,
    )
    if cooldown is not None:
        since = datetime.now(timezone.utc) - timedelta(days=cooldown)
        q     = q.filter(ChkdEmail.sent_at >= since)
    return q.first() is not None


def send_chkd_email(
    db,
    user_id:    str,
    to_email:   str,
    email_type: str,
    subject:    str,
    body:       str,
) -> bool:
    if _already_sent(db, user_id, email_type):
        return False
    ok = send_email(to_email, subject, body, from_name="Tommy")
    if ok:
        db.add(ChkdEmail(user_id=user_id, email=to_email, email_type=email_type))
        db.commit()
    return ok


# ── Daily sweep (called by APScheduler job) ───────────────────────────────────

def run_daily_checks() -> Dict[str, int]:
    """
    Sweeps all CHKD users for Day-3 re-engagement and Day-7 streak triggers.
    Returns a count dict for logging.
    """
    from db import SessionLocal

    profiles = get_all_profiles()
    if not profiles:
        print("[chkd] No profiles returned — skipping daily sweep")
        return {}

    recent_days = get_recent_days(_DAYS_LOOKBACK)
    days_by_user: Dict[str, List[Dict]] = {}
    for row in recent_days:
        uid = row.get(_DAYS_USER_ID)
        if uid:
            days_by_user.setdefault(uid, []).append(row)

    counts = {"day3_reengagement": 0, "day7_streak": 0}
    db     = SessionLocal()
    try:
        for p in profiles:
            uid       = p.get(_PROFILES_ID, "")
            email     = p.get(_PROFILES_EMAIL, "")
            name      = p.get(_PROFILES_NAME) or email
            created   = p.get(_PROFILES_CREATED, "")
            user_days = days_by_user.get(uid, [])

            if not uid or not email:
                continue

            if _has_streak(user_days, 7):
                subj, body = build_streak_email(name, 7)
                if send_chkd_email(db, uid, email, "day7_streak", subj, body):
                    counts["day7_streak"] += 1

            elif _needs_reengagement(user_days, created):
                subj, body = build_reengagement_email(name)
                if send_chkd_email(db, uid, email, "day3_reengagement", subj, body):
                    counts["day3_reengagement"] += 1

    except Exception as exc:
        db.rollback()
        print(f"[chkd] ERROR run_daily_checks: {exc}")
    finally:
        db.close()

    print(f"[chkd] Daily sweep complete: {counts}")
    return counts
