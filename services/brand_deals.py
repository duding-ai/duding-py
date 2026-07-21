"""
services/brand_deals.py — Get CHKD Brand Deals Agent

Reuses the Duding outreach engine's skeleton (prospector -> verify ->
personalize -> send -> track) retargeted at brand partnerships instead
of trade-business leads. Thin job_brand_prospector / job_brand_pitch_sender
wrappers live in outreach_engine.py and call run_prospector() /
run_pitch_sender() here, mirroring how job_social_intelligence wraps
services/social_intelligence.py.

Required env vars:
  ANTHROPIC_API_KEY        — pitch personalization
  CHKD_RESEND_API_KEY      — sending (same Resend account as CHKD emails)
  BRAND_DEALS_FROM_EMAIL   — MUST be a verified sender on a subdomain
                              separate from tommy@getchkd.app (protects
                              that domain's transactional deliverability).
                              Sending is disabled until this is set.
  BRAND_DEALS_DAILY_CAP    — default 10
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from db import SessionLocal
from models.brand_prospect import BrandProspect
from models.brand_outreach_email import BrandOutreachEmail
from services.email import send_email

CHKD_CLIENT_ID = 1
SEED_LIST_PATH = Path(__file__).resolve().parent.parent / "data" / "brand_seed_list.json"

# ── Pitch kit — edit this to update Tommy's brand-deals positioning ────────

PITCH_KIT: Dict[str, Any] = {
    "founder_name": "Tommy Campos",
    "positioning": (
        "Founder of Get CHKD (getchkd.app), a daily accountability app for men "
        "built around three non-negotiables: Faith, Fitness, Business. Creates "
        "daily content in the men's discipline / self-improvement space."
    ),
    "audience_stats": [
        "25-34 core demographic (48-75% of viewers per video)",
        "54-77% male",
        "95%+ US audience",
        "engaged sharers — 15-20% share rate on top-performing videos",
    ],
    "offers": ["product seeding", "UGC content packages", "affiliate partnership"],
    "tone_notes": (
        "Lead with founder authenticity + exact-fit audience + content quality. "
        "NOT claiming big reach — this is a small, engaged, values-aligned audience pitch."
    ),
    "links": {
        "app": "https://getchkd.app",
        "instagram": "@getchkd",
        "tiktok": "@getchkd",
        "founder_instagram": "@officialtommycampos",
    },
}


# Domains that must NEVER be used as the Brand Deals sending address,
# even if BRAND_DEALS_FROM_EMAIL is somehow set to one of them — cold
# outreach must not touch either transactional domain's reputation.
# No override, no fallback: if the configured address resolves to one
# of these, sending is refused outright.
_PROTECTED_DOMAINS = {"getchkd.app", "duding.ai"}


def _from_email() -> str:
    """Returns the address to send from, or "" if sending isn't
    allowed — which happens if BRAND_DEALS_SENDING_ENABLED isn't
    explicitly "true" (defaults OFF), BRAND_DEALS_FROM_EMAIL isn't
    set, or its domain is one of the protected domains above. Every
    caller in this module treats "" as "do not send", so this is the
    single choke point for the whole gate."""
    if os.getenv("BRAND_DEALS_SENDING_ENABLED", "").strip().lower() != "true":
        return ""
    addr = os.getenv("BRAND_DEALS_FROM_EMAIL", "").strip()
    if not addr:
        return ""
    domain = addr.split("@")[-1].strip().lower().rstrip(">")
    if domain in _PROTECTED_DOMAINS:
        print(f"[brand_deals] REFUSING to send — BRAND_DEALS_FROM_EMAIL domain '{domain}' is a protected domain, not a dedicated subdomain.")
        return ""
    return addr


def _daily_cap() -> int:
    try:
        return int(os.getenv("BRAND_DEALS_DAILY_CAP", "10"))
    except ValueError:
        return 10


# ── Seed list + discovery ────────────────────────────────────────────────────

def load_seed_brands() -> List[Dict[str, str]]:
    try:
        data = json.loads(SEED_LIST_PATH.read_text(encoding="utf-8"))
        return data.get("brands", [])
    except Exception as exc:
        print(f"[brand_deals] could not load seed list: {exc}")
        return []


def discover_brand_mentions_from_social_intel(db) -> List[Dict[str, str]]:
    """
    Best-effort: scans social_intelligence_reports.raw_data for @handles
    that look like brand tags in competitor post text. The Social
    Intelligence Agent currently runs on mock competitor data (see
    services/social_intelligence.py) which doesn't include real brand
    tags, so this returns [] until real Apify scraping is activated —
    it's wired up now so it starts working automatically once that
    switch is flipped, no brand_deals changes needed.
    """
    import re
    from models.social_intelligence_report import SocialIntelligenceReport

    found: Dict[str, Dict[str, str]] = {}
    reports = (
        db.query(SocialIntelligenceReport)
        .order_by(SocialIntelligenceReport.week_of.desc())
        .limit(8)
        .all()
    )
    handle_re = re.compile(r"@([a-zA-Z0-9_.]{2,30})")
    for report in reports:
        if not report.raw_data:
            continue
        try:
            posts = json.loads(report.raw_data)
        except Exception:
            continue
        if not isinstance(posts, list):
            continue
        for post in posts:
            text = (post or {}).get("text", "") if isinstance(post, dict) else ""
            for handle in handle_re.findall(text):
                key = handle.lower()
                if key not in found:
                    found[key] = {
                        "brand_name": handle,
                        "website": f"https://www.instagram.com/{handle}",
                        "industry": "other",
                    }
    return list(found.values())


def _domain_of(url: str) -> str:
    try:
        from app import _prospect_domain
        return _prospect_domain(url)
    except Exception:
        from urllib.parse import urlparse
        try:
            return urlparse(url if "://" in url else "https://" + url).netloc.lower().replace("www.", "")
        except Exception:
            return ""


def _existing_domains(db) -> Set[str]:
    rows = db.query(BrandProspect.website).filter(BrandProspect.website.isnot(None)).all()
    return {_domain_of(w) for (w,) in rows if w}


# ── Prospector ────────────────────────────────────────────────────────────

def run_prospector(max_candidates: int = 15) -> Dict[str, int]:
    """Works through the seed list (then mention-discovery), finds a
    contact email for each new brand, validates MX, classifies
    direct/generic, and inserts a BrandProspect row. Direct emails land
    as 'verified' (sendable); generic emails land as 'held_for_review'."""
    from app import _scrape_contact_email
    from outreach_engine import has_mx_record

    db = SessionLocal()
    counts = {"checked": 0, "verified": 0, "held_for_review": 0, "skipped_no_email": 0}
    try:
        existing = _existing_domains(db)
        candidates = load_seed_brands() + discover_brand_mentions_from_social_intel(db)

        checked = 0
        for brand in candidates:
            if checked >= max_candidates:
                break
            website = brand.get("website", "")
            domain = _domain_of(website)
            if not domain or domain in existing:
                continue
            existing.add(domain)
            checked += 1
            counts["checked"] += 1

            email, quality, note = _scrape_contact_email(website)
            mx_ok = bool(email) and has_mx_record(email)

            if not email or not mx_ok:
                counts["skipped_no_email"] += 1
                continue

            status = "verified" if quality == "direct" else "held_for_review"
            counts["verified" if status == "verified" else "held_for_review"] += 1

            db.add(BrandProspect(
                client_id=CHKD_CLIENT_ID,
                brand_name=brand.get("brand_name") or domain,
                website=website,
                industry=brand.get("industry") or "other",
                contact_email=email,
                email_type=quality,
                source="seed_list" if "instagram.com" not in website else "competitor_mention",
                status=status,
                notes=note or None,
            ))
        db.commit()
    except Exception as exc:
        db.rollback()
        print(f"[brand_deals] prospector error: {exc}")
    finally:
        db.close()

    return counts


# ── Pitch generation ─────────────────────────────────────────────────────────

def generate_pitch(prospect: BrandProspect) -> tuple[str, str]:
    """Returns (subject, body) — body excludes the CAN-SPAM footer,
    which is appended by the caller right before sending."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return _fallback_pitch(prospect)

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        prompt = (
            f"Write a short cold outreach email pitching a brand partnership.\n\n"
            f"Sender: {PITCH_KIT['founder_name']} — {PITCH_KIT['positioning']}\n"
            f"Audience data to cite (pick 1-2, don't dump all of them): {'; '.join(PITCH_KIT['audience_stats'])}\n"
            f"Offers: {', '.join(PITCH_KIT['offers'])}\n"
            f"Tone: {PITCH_KIT['tone_notes']}\n"
            f"Links to include: {PITCH_KIT['links']['app']}, "
            f"{PITCH_KIT['links']['instagram']} (Instagram), {PITCH_KIT['links']['tiktok']} (TikTok)\n\n"
            f"Recipient brand: {prospect.brand_name}"
            f"{' (' + prospect.industry + ')' if prospect.industry else ''}"
            f"{' — ' + prospect.website if prospect.website else ''}\n\n"
            f"Requirements: plain text, no hype/spam tone, under 150 words, ends with the sender's first name only "
            f"(no formal sign-off block, no footer — that gets appended separately). "
            f"Respond ONLY with JSON: {{\"subject\": \"...\", \"body\": \"...\"}}"
        )

        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=600,
            system="You write concise, founder-voiced cold outreach emails for brand partnerships. No hype, no emojis, no exclamation points.",
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text if message.content else ""
        text = raw.strip().strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
        data = json.loads(text)
        subject = data.get("subject") or f"Partnership idea — {prospect.brand_name} x Get CHKD"
        body = data.get("body") or _fallback_pitch(prospect)[1]
        return subject, body
    except Exception as exc:
        print(f"[brand_deals] pitch generation failed for {prospect.brand_name}: {exc}")
        return _fallback_pitch(prospect)


def _fallback_pitch(prospect: BrandProspect) -> tuple[str, str]:
    subject = f"Partnership idea — {prospect.brand_name} x Get CHKD"
    body = (
        f"Hey,\n\n"
        f"I run Get CHKD ({PITCH_KIT['links']['app']}) — a daily accountability app for men built "
        f"around Faith, Fitness, and Business. I create daily content in the men's discipline space "
        f"and think {prospect.brand_name} could be a strong fit for my audience "
        f"(25-34, mostly male, engaged — top videos run 15-20% share rate).\n\n"
        f"Open to product seeding, a UGC content package, or an affiliate partnership — whatever's "
        f"easiest on your end to start.\n\n"
        f"{PITCH_KIT['links']['instagram']} / {PITCH_KIT['links']['tiktok']}\n\n"
        f"{PITCH_KIT['founder_name'].split()[0]}"
    )
    return subject, body


# ── Sender (+ one automatic follow-up) ───────────────────────────────────────

def _is_business_hours_et() -> bool:
    try:
        from zoneinfo import ZoneInfo
        now_et = datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        now_et = datetime.now(timezone.utc)
    return 9 <= now_et.hour < 18


def _today_brand_email_count(db) -> int:
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return db.query(BrandOutreachEmail).filter(BrandOutreachEmail.sent_at >= today).count()


def run_pitch_sender() -> Dict[str, int]:
    from app import _can_spam_footer

    counts = {"sent": 0, "followups": 0, "queued_no_dns": 0}
    from_email = _from_email()
    resend_key = os.getenv("CHKD_RESEND_API_KEY", "")

    db = SessionLocal()
    try:
        cap = _daily_cap()
        sent_today = _today_brand_email_count(db)
        if sent_today >= cap:
            return counts

        # 1) Due follow-ups first (sent >=5 business days ago, no reply, step 1 only)
        followup_cutoff = datetime.now(timezone.utc) - timedelta(days=7)  # ~5 business days
        due_followups = (
            db.query(BrandProspect)
            .filter(
                BrandProspect.status == "sent",
                BrandProspect.last_contacted_at.isnot(None),
                BrandProspect.last_contacted_at <= followup_cutoff,
            )
            .all()
        )
        for prospect in due_followups:
            if sent_today + counts["sent"] + counts["followups"] >= cap:
                break
            already_followed_up = any(e.sequence_step >= 2 for e in prospect.emails)
            if already_followed_up:
                continue
            if not from_email:
                continue
            subject, body = generate_pitch(prospect)
            subject = f"Re: {subject}"
            body = f"Following up on this — still open if it's useful:\n\n{body}"
            full_body = body + _can_spam_footer()
            ok = send_email(prospect.contact_email, subject, full_body,
                             from_name=PITCH_KIT["founder_name"].split()[0], from_email=from_email,
                             api_key=resend_key)
            if ok:
                prospect.last_contacted_at = datetime.now(timezone.utc)
                db.add(BrandOutreachEmail(
                    prospect_id=prospect.id, subject=subject, body=full_body, sequence_step=2,
                ))
                counts["followups"] += 1
                db.commit()

        # 2) Fresh sends — verified prospects not yet contacted
        candidates = (
            db.query(BrandProspect)
            .filter(BrandProspect.status.in_(["verified", "queued"]))
            .order_by(BrandProspect.found_at.asc())
            .limit(cap * 2)
            .all()
        )
        for prospect in candidates:
            if sent_today + counts["sent"] + counts["followups"] >= cap:
                break
            if not prospect.contact_email:
                continue
            if not from_email:
                if prospect.status != "queued":
                    prospect.status = "queued"
                    counts["queued_no_dns"] += 1
                    db.commit()
                continue

            subject, body = generate_pitch(prospect)
            full_body = body + _can_spam_footer()
            ok = send_email(prospect.contact_email, subject, full_body,
                             from_name=PITCH_KIT["founder_name"].split()[0], from_email=from_email,
                             api_key=resend_key)
            if ok:
                prospect.status = "sent"
                prospect.last_contacted_at = datetime.now(timezone.utc)
                db.add(BrandOutreachEmail(
                    prospect_id=prospect.id, subject=subject, body=full_body, sequence_step=1,
                ))
                counts["sent"] += 1
                db.commit()
    except Exception as exc:
        db.rollback()
        print(f"[brand_deals] sender error: {exc}")
    finally:
        db.close()

    if counts["queued_no_dns"]:
        print(f"[brand_deals] {counts['queued_no_dns']} prospect(s) queued — sending gate closed (BRAND_DEALS_SENDING_ENABLED/BRAND_DEALS_FROM_EMAIL not set, or a protected domain)")

    return counts


# ── Dashboard summary ────────────────────────────────────────────────────────

def get_pipeline_counts(db) -> Dict[str, int]:
    from sqlalchemy import func as _func
    rows = (
        db.query(BrandProspect.status, _func.count(BrandProspect.id))
        .filter(BrandProspect.client_id == CHKD_CLIENT_ID)
        .group_by(BrandProspect.status)
        .all()
    )
    counts = {status: n for status, n in rows}
    counts["dns_ready"] = bool(_from_email())
    return counts


def get_briefing_summary(db) -> Dict[str, int]:
    """For the daily 8am briefing email."""
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return {
        "new_prospects_today": db.query(BrandProspect).filter(BrandProspect.found_at >= today).count(),
        "sent_today": db.query(BrandOutreachEmail).filter(BrandOutreachEmail.sent_at >= today).count(),
        "held_for_review": db.query(BrandProspect).filter(BrandProspect.status == "held_for_review").count(),
        "replied": db.query(BrandProspect).filter(BrandProspect.status == "replied").count(),
    }
