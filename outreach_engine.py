"""
outreach_engine.py — Duding.ai Autonomous Business Engine

SECTION 1  Outreach: find → scrape → email → follow-up (APScheduler)
SECTION 2  Reply detection: IMAP, sentiment, Calendly, draft responses, SMS
SECTION 3  Daily briefing: 8am email to Tommy
SECTION 4  Client onboarding: triggered by Stripe webhook
SECTION 5  Build status: Day 3 / 7 / 10 emails
SECTION 6  Testimonial & referral: 7 days post-completion
SECTION 7  Upsell: 30 / 37 days post-live
SECTION 8  Content: Instagram captions, video hooks, content angles
SECTION 9  Domain watcher: Resend verification loop (self-removes when live)
"""

from __future__ import annotations

import email as email_lib
import imaplib
import os
import re
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from email.header import decode_header as _hdr_decode
from email.utils import parseaddr
from typing import Any, Deque, Dict, List, Optional, Set

from services.email import send_email as _resend_email

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from db import SessionLocal
from models.outreach_activity import OutreachActivity
from models.outreach_prospect import OutreachProspect
from models.build import Build
from models.content_idea import ContentIdea
from models.client_draft import ClientDraft
from models.referral import Referral

# ── Constants ─────────────────────────────────────────────────────────────────

TOMMY_EMAIL = "tomcampos1@aol.com"
DAILY_SEND_LIMIT = 50
CALENDLY_LINK = os.getenv("CALENDLY_LINK", "https://calendly.com/duding")
RESEND_DOMAIN_ID = os.getenv("RESEND_DOMAIN_ID", "3f79c9ae-63d8-495c-a79c-9fc4f05f88c2")

TRADES: List[str] = [
    "plumber", "HVAC contractor", "electrician", "roofing contractor",
    "drain cleaning service", "water heater repair", "AC repair",
    "heating cooling company", "sewer repair", "pest control",
    "landscaping company", "lawn care service", "plumbing service",
    "electrical contractor",
]
LOCATIONS: List[str] = [
    "Texas", "Florida", "Georgia", "Ohio", "North Carolina",
    "Pennsylvania", "Illinois", "Michigan", "Arizona", "Tennessee",
    "Missouri", "Virginia", "Indiana", "Wisconsin", "Colorado",
    "Maryland", "Nevada", "Kentucky", "Minnesota", "Alabama",
    "South Carolina", "Louisiana", "Oregon", "Oklahoma", "Connecticut",
    "Iowa", "Mississippi", "Arkansas", "Kansas", "Utah",
]

# ── Engine state ──────────────────────────────────────────────────────────────

_state: Dict[str, Any] = {
    "running": False,
    "paused": False,
    "trade_idx": 0,
    "location_idx": 0,
    "last_find_at": None,
    "last_send_at": None,
    "last_reply_check_at": None,
    "last_summary_at": None,
    "last_build_check_at": None,
}
_log_ring: Deque[str] = deque(maxlen=300)
_scheduler: Optional[BackgroundScheduler] = None


# ── DB schema bootstrap ───────────────────────────────────────────────────────



# ── Core helpers ──────────────────────────────────────────────────────────────

def _log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    entry = f"[{ts}] {msg}"
    _log_ring.append(entry)
    print(f"[engine] {entry}")


def _send(to_email: str, subject: str, body: str, from_name: str = "Tommy") -> bool:
    return _resend_email(to_email, subject, body, from_name=from_name)


def _send_sms(short_msg: str) -> bool:
    """
    SECTION 9 — SMS via email-to-SMS gateway.
    Set TOMMY_SMS_EMAIL in .env, e.g. 5551234567@txt.att.net
    """
    sms_addr = os.getenv("TOMMY_SMS_EMAIL", "").strip()
    if not sms_addr:
        return False
    return _send(sms_addr, "Duding", short_msg[:160], from_name="Duding")


def _today_sent_count(db) -> int:
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        db.query(OutreachActivity)
        .filter(
            OutreachActivity.activity_type.in_(["email_sent", "follow_up_sent"]),
            OutreachActivity.created_at >= today,
        )
        .count()
    )


def _under_limit(db) -> bool:
    n = _today_sent_count(db)
    if n >= DAILY_SEND_LIMIT:
        _log(f"Daily limit reached ({n}/{DAILY_SEND_LIMIT})")
        return False
    return True


def _next_term() -> str:
    trade    = TRADES[_state["trade_idx"] % len(TRADES)]
    location = LOCATIONS[_state["location_idx"] % len(LOCATIONS)]
    _state["trade_idx"] += 1
    if _state["trade_idx"] % len(TRADES) == 0:
        _state["location_idx"] = (_state["location_idx"] + 1) % len(LOCATIONS)
    return f"{trade} {location}"


def _existing_domains(db) -> Set[str]:
    from app import _prospect_domain
    rows = (
        db.query(OutreachProspect.website)
        .filter(OutreachProspect.website.isnot(None))
        .all()
    )
    return {_prospect_domain(w) for (w,) in rows if w}


# ── IMAP / reply helpers ──────────────────────────────────────────────────────

def _decode_hdr(raw: Optional[str]) -> str:
    if not raw:
        return ""
    parts = []
    for chunk, charset in _hdr_decode(raw):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(str(chunk))
    return "".join(parts)


def _body_text(msg: email_lib.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                pl = part.get_payload(decode=True)
                if pl:
                    return pl.decode(part.get_content_charset() or "utf-8", errors="replace")
    else:
        pl = msg.get_payload(decode=True)
        if pl:
            return pl.decode(msg.get_content_charset() or "utf-8", errors="replace")
    return ""


def _match_prospect(from_header: str, subject: str, db) -> Optional[OutreachProspect]:
    _, from_email = parseaddr(from_header)
    from_email = (from_email or "").lower().strip()

    p = db.query(OutreachProspect).filter(
        OutreachProspect.email.ilike(from_email)
    ).first()
    if p:
        return p

    if "@" in from_email:
        domain = from_email.split("@", 1)[1]
        p = db.query(OutreachProspect).filter(
            OutreachProspect.email.ilike(f"%@{domain}")
        ).first()
        if p:
            return p

    clean = re.sub(r"^(re|fw|fwd):\s*", "", (subject or "").strip(), flags=re.IGNORECASE)
    if clean:
        p = db.query(OutreachProspect).filter(
            OutreachProspect.last_email_subject == clean
        ).first()
        if p:
            return p

    return None


def _detect_sentiment(body: str) -> str:
    """Return 'positive', 'negative', or 'neutral'."""
    b = body.lower()
    neg = [
        "not interested", "unsubscribe", "remove me", "stop emailing",
        "don't contact", "no thanks", "no thank you", "please don't",
        "spam", "do not email", "take me off",
    ]
    pos = [
        "interested", "yes,", "yes!", "sounds good", "tell me more", "how much",
        "what's the price", "what does it cost", "love to", "book", "schedule",
        "let's chat", "when are you", "available", "would love", "great idea",
        "makes sense", "send me", "more info", "want to learn",
    ]
    for w in neg:
        if w in b:
            return "negative"
    for w in pos:
        if w in b:
            return "positive"
    return "neutral"


# ── SECTION 8 — Content generation ───────────────────────────────────────────

def _gen_instagram_captions(biz_name: str, trade: str, description: str) -> List[str]:
    t = trade or "service"
    return [
        (
            f"Before working with us: missed calls, slow follow-up, lost jobs.\n"
            f"After: every lead gets a text-back in 60 seconds — automated follow-up runs for 7 days.\n\n"
            f"Just set this up for a {t} business. Ask me how it works. 🔧"
        ),
        (
            f"The average home-service business loses 3 out of 10 leads to slow follow-up.\n\n"
            f"We install systems that fix that — auto text-back, follow-up sequences, booking automation.\n\n"
            f"Runs in the background so you focus on the job site. 📱\n"
            f"#homeservice #{t.replace(' ', '')} #leadgeneration"
        ),
        (
            f"Had a call with a {t} owner last week.\n\n"
            f"He said: \"I know I'm losing leads but I don't have time to follow up.\"\n\n"
            f"That's exactly what we fix. System went live. Leads captured automatically.\n\n"
            f"Comment INFO if you want to see how it works. 💡"
        ),
    ]


def _gen_video_hooks(outreach_count: int, reply_count: int, top_trade: str) -> List[str]:
    t = top_trade or "service business"
    return [
        f"I sent {outreach_count} cold emails to {t} owners last week. Here's what their replies revealed about the #1 problem in the trade space right now...",
        f"What would it mean for your {t} to never lose a lead to voicemail again? I'll show you the exact system in 60 seconds.",
        f"I looked at {outreach_count} local service businesses this week. Most had zero automated follow-up. Here's what that's actually costing them.",
        f"The {t} owner who replied to my cold email and booked in 4 hours — here's what made them say yes.",
        f"Most {t} owners think they have a lead volume problem. They actually have a follow-up speed problem. Here's the difference.",
    ]


# ── SECTION 4 — Client onboarding email builders ─────────────────────────────

def _welcome_email(build: Build):
    name = build.contact_name or "there"
    biz  = build.business_name or "your business"
    days = build.timeline_days or 10
    pkg  = (build.package_tier or "starter").title()
    subj = f"Welcome to Duding, {biz} — your build slot is locked"
    body = (
        f"Hi {name},\n\n"
        f"You're in. Deposit confirmed, {pkg} build slot locked.\n\n"
        f"Here's what happens next:\n\n"
        f"1. You'll get a short questionnaire in the next few minutes — takes 5 min\n"
        f"2. I'll start building your lead system immediately\n"
        f"3. You'll get updates at day 3, day 7, and when you go live (within {days} days)\n\n"
        f"If anything comes up, just reply to this email. I check it daily.\n\n"
        f"Excited to build this for you,\n"
        f"Tommy\n\n"
        f"P.S. You don't need to do anything right now. I'll reach out when I need your input."
    )
    return subj, body


def _questionnaire_email(build: Build):
    name = build.contact_name or "there"
    biz  = build.business_name or "your business"
    subj = f"{biz} — quick build questionnaire (5 min)"
    body = (
        f"Hi {name},\n\n"
        f"To build your lead system correctly, I need a few quick answers. Just reply to this email:\n\n"
        f"1. What phone number should new leads be sent to (calls/texts)?\n"
        f"2. What are your working hours? (e.g. Mon–Fri 8am–6pm)\n"
        f"3. What city/area do you serve?\n"
        f"4. What's the #1 service you want to capture leads for?\n"
        f"5. Do you have a specific offer for new leads? (e.g. free estimate, 10% off) or should I write one?\n\n"
        f"Takes about 5 minutes. The sooner I get this, the faster we go live.\n\n"
        f"Thanks,\n"
        f"Tommy"
    )
    return subj, body


# ── SECTION 5 — Build status email builders ───────────────────────────────────

def _build_day3_email(build: Build):
    name = build.contact_name or "there"
    biz  = build.business_name or "your business"
    subj = f"{biz} build — day 3 update"
    body = (
        f"Hi {name},\n\n"
        f"Quick update — day 3 into your build and everything is on track.\n\n"
        f"Currently working on: lead capture setup and your custom follow-up sequences.\n\n"
        f"I'll send a preview once the core is ready. No action needed from you.\n\n"
        f"Tommy"
    )
    return subj, body


def _build_day7_email(build: Build):
    name = build.contact_name or "there"
    biz  = build.business_name or "your business"
    subj = f"{biz} — preview is ready"
    body = (
        f"Hi {name},\n\n"
        f"Day 7 — here's where your lead system stands:\n\n"
        f"✓ Lead capture form — built and tested\n"
        f"✓ Auto text-back — fires within 60 seconds of a new lead\n"
        f"✓ Follow-up sequence — Day 1 / Day 3 / Day 7 messages written\n"
        f"✓ Lead notifications — you'll get an alert the moment a lead comes in\n\n"
        f"Final setup and testing underway. Should be live very soon.\n\n"
        f"Tommy"
    )
    return subj, body


def _build_day10_email(build: Build):
    name  = build.contact_name or "there"
    biz   = build.business_name or "your business"
    trade = build.business_type or "service"
    subj  = f"{biz} — your lead system is LIVE"
    body  = (
        f"Hi {name},\n\n"
        f"Your lead system is live.\n\n"
        f"✓ Every new lead gets a text-back within 60 seconds\n"
        f"✓ Automated follow-up runs for 7 days after first contact\n"
        f"✓ You get notified immediately for every new lead\n\n"
        f"If you want to adjust anything — messaging, timing, offer — just reply here.\n\n"
        f"Thanks for trusting me with this. Genuinely excited to see it work for {biz}.\n\n"
        f"Tommy\n\n"
        f"P.S. If you know any other {trade} businesses losing leads to voicemail, "
        f"I offer a $250 account credit for every referral that pays a deposit."
    )
    return subj, body


# ── SECTION 6 — Testimonial + referral email builder ─────────────────────────

def _testimonial_email(build: Build):
    name  = build.contact_name or "there"
    biz   = build.business_name or "your business"
    trade = build.business_type or "service"
    subj  = f"How's the system working for {biz}?"
    body  = (
        f"Hi {name},\n\n"
        f"About a week since your system went live — how's it going?\n\n"
        f"If you've had a good experience, I'd love a quick testimonial — just a sentence or two "
        f"about what changed. Helps other {trade} owners decide. You can reply right here.\n\n"
        f"Also — if you know any service businesses losing leads to voicemail or slow follow-up, "
        f"I offer a $250 account credit for every referral that pays a deposit. "
        f"Just have them mention your name or forward this email.\n\n"
        f"Thanks for building with me,\n"
        f"Tommy"
    )
    return subj, body


# ── SECTION 7a — Retainer upsell email builder ───────────────────────────────

APP_BASE_URL = os.getenv("APP_BASE_URL", "https://duding.ai")


def _retainer_upsell_email(build: Build):
    name = build.contact_name or "there"
    biz  = build.business_name or "your business"
    bid  = build.build_id

    growth_link = f"{APP_BASE_URL}/retainer/accept/{bid}/growth"
    scale_link  = f"{APP_BASE_URL}/retainer/accept/{bid}/scale"

    subj = f"{biz} — keep the momentum going"
    body = (
        f"Hi {name},\n\n"
        f"Your lead intake system is live. Every inbound lead is being captured, "
        f"followed up, and routed. The hard part is done.\n\n"
        f"Now the question is: what demand are you sending into it?\n\n"
        f"I have two ongoing growth options for clients whose systems are live:\n\n"
        f"── Growth Retainer · $997/month ──\n"
        f"• Google + Meta ad management\n"
        f"• Monthly content calendar (built from your real business data)\n"
        f"• Performance reporting tied directly to your dashboard\n"
        f"  (leads → deposits → booked revenue — not vanity metrics)\n\n"
        f"Accept Growth Retainer → {growth_link}\n\n"
        f"── Scale Retainer · $1,497/month ──\n"
        f"• Everything in Growth\n"
        f"• Full brand build-out (social, website, creative)\n"
        f"• Bi-weekly strategy calls\n"
        f"• Priority support\n\n"
        f"Accept Scale Retainer → {scale_link}\n\n"
        f"Both options start after a short onboarding — I collect your ad account "
        f"info and brand assets, and we're running within the week.\n\n"
        f"No obligation. Just click the link for whichever tier makes sense and "
        f"fill out a quick form. I'll follow up within 24 hours to confirm.\n\n"
        f"Tommy"
    )
    return subj, body


# ── SECTION 7 — Upsell email builder ─────────────────────────────────────────

def _upsell_email(build: Build, is_followup: bool = False):
    name  = build.contact_name or "there"
    biz   = build.business_name or "your business"
    pkg   = (build.package_tier or "starter").title()
    tag   = "one more thought" if is_followup else "quick check-in"
    weeks = "5 weeks" if is_followup else "a month"
    subj  = f"{biz} — {tag}"
    body  = (
        f"Hi {name},\n\n"
        f"{'Following up on my last note — ' if is_followup else ''}"
        f"It's been about {weeks} since your lead system went live at {biz}.\n\n"
        f"You're on the {pkg} plan. A lot of clients at this stage add:\n\n"
        f"• SMS broadcast sequences\n"
        f"• CRM pipeline integration\n"
        f"• Automated booking calendar\n"
        f"• Google review request automation\n\n"
        f"If any of that sounds interesting, I have a few slots open this week: {CALENDLY_LINK}\n\n"
        f"No pressure — just checking in.\n\n"
        f"Tommy"
    )
    return subj, body


# ── SECTION 4 (public) — trigger_onboarding ──────────────────────────────────

def trigger_onboarding(build_id: int) -> None:
    """
    Called by the Stripe webhook after deposit is confirmed.
    Sends welcome + questionnaire to client, captions + SMS to Tommy.
    """
    db = SessionLocal()
    try:
        build = db.query(Build).filter(Build.id == build_id).first()
        if not build:
            _log(f"Onboarding: build {build_id} not found")
            return
        if build.onboarding_sent:
            _log(f"Onboarding already sent for build {build_id}")
            return

        biz_name = build.business_name or build.email
        trade    = build.business_type or "service"

        # Welcome email
        subj, body = _welcome_email(build)
        ok1 = _send(build.email, subj, body, from_name="Tommy")

        time.sleep(3)

        # Questionnaire email
        subj2, body2 = _questionnaire_email(build)
        ok2 = _send(build.email, subj2, body2, from_name="Tommy")

        build.onboarding_sent = True
        build.status = "INSTALLING"
        db.add(build)
        db.commit()

        _log(f"{'✓' if ok1 and ok2 else '~'} Onboarding → {build.email} ({biz_name})")

        # SECTION 9 — SMS to Tommy
        _send_sms(f"New client! {biz_name} paid deposit. Build #{build_id} started.")

        # SECTION 8 — Instagram captions to Tommy
        captions = _gen_instagram_captions(biz_name, trade, "")
        caption_body = (
            f"New deposit from {biz_name} ({trade}).\n\n"
            f"3 Instagram caption ideas:\n\n"
            + "\n\n──────────\n\n".join(
                f"Option {i+1}:\n{c}" for i, c in enumerate(captions)
            )
            + "\n\nPick one, customize, and post. 🎉"
        )
        _send(
            TOMMY_EMAIL,
            f"[Content] 3 IG captions — {biz_name} deposit",
            caption_body,
            from_name="Duding",
        )

    except Exception as exc:
        db.rollback()
        _log(f"ERROR trigger_onboarding: {exc}")
    finally:
        db.close()


# ── Scheduled jobs ────────────────────────────────────────────────────────────

def job_find_prospects() -> None:
    """SECTION 1 — DDG search, domain dedup, queue new prospects + content angle."""
    if _state["paused"]:
        return

    from app import find_business_prospects, _prospect_domain

    term = _next_term()
    _log(f"Finding: {term!r}")

    db = SessionLocal()
    try:
        existing = _existing_domains(db)
        urls = find_business_prospects(term, max_results=10)
        added = 0

        for url in urls:
            domain = _prospect_domain(url)
            if not domain or domain in existing:
                continue
            existing.add(domain)
            db.add(OutreachProspect(
                source_input=term,
                source_url=url,
                website=url,
                email=f"info@{domain}",
                status="queued",
            ))
            added += 1

        db.commit()
        _state["last_find_at"] = datetime.now(timezone.utc)
        _log(f"Queued {added} new prospect(s) from {term!r}")

        # SECTION 8 — Save content angle idea
        if added > 0:
            parts      = term.split(" ", 1)
            trade_part = parts[0]
            loc_part   = parts[1] if len(parts) > 1 else ""
            db.add(ContentIdea(
                idea_type="content_angle",
                content=(
                    f"Found {added} {trade_part} businesses in {loc_part} with no automated lead follow-up.\n"
                    f"Angle: \"What {added} {trade_part} owners told me about their #1 business problem this week.\""
                ),
                source=term,
            ))
            db.commit()

    except Exception as exc:
        db.rollback()
        _log(f"ERROR find_prospects: {exc}")
    finally:
        db.close()


def job_send_next_queued() -> None:
    """SECTION 1 — Scrape + email the oldest queued prospect (respects 50/day limit)."""
    if _state["paused"]:
        return

    from app import (
        scrape_business_profile,
        build_outreach_email,
        _clean_business_name,
        _get_outreach_email_for_target,
        send_email as _app_send_email,
    )

    db = SessionLocal()
    try:
        if not _under_limit(db):
            return

        prospect = (
            db.query(OutreachProspect)
            .filter(OutreachProspect.status == "queued")
            .order_by(OutreachProspect.created_at.asc())
            .first()
        )
        if not prospect:
            return

        url = prospect.source_url or prospect.website or ""
        _log(f"Sending to {url}")

        profile       = scrape_business_profile(url)
        resolved_url  = profile.get("final_url") or url
        to_email, email_quality, email_note = _get_outreach_email_for_target(url, resolved_url)
        subject, body = build_outreach_email(profile, url)
        biz_name      = _clean_business_name(
            profile.get("business_name") or profile.get("website_title") or url
        )
        description  = profile.get("website_description") or profile.get("industry") or ""
        is_generic   = email_quality == "generic"

        prospect.business_name        = biz_name or prospect.business_name
        prospect.email                = to_email
        prospect.email_quality        = email_quality
        prospect.email_note           = email_note
        prospect.website              = profile.get("final_url") or resolved_url or prospect.website
        prospect.business_description = description[:1000]
        prospect.lever                = subject
        prospect.last_email_subject   = subject
        prospect.last_message         = body
        prospect.next_follow_up_at    = datetime.now(timezone.utc) + timedelta(days=3)
        prospect.status               = "pending_review" if is_generic else "outreach_pending"

        if not is_generic:
            prospect.last_contacted_at = datetime.now(timezone.utc)
            db.add(OutreachActivity(
                prospect_id=prospect.id, activity_type="email_sent",
                subject=subject, body_preview=body[:180], status="sent",
            ))
        db.commit()

        if not is_generic:
            ok = _send(to_email, subject, body, from_name="Tommy")
            _state["last_send_at"] = datetime.now(timezone.utc)
            _log(f"{'✓' if ok else '✗'} {biz_name} → {to_email}")
        else:
            _log(f"✋ {biz_name} → {to_email} [generic — held for review]")

        # SECTION 8 — Content angle if business mentions years of experience
        years_match = re.search(r"(\d{1,2})\s*\+?\s*years", description, re.IGNORECASE)
        if years_match and biz_name:
            years = years_match.group(1)
            db.add(ContentIdea(
                idea_type="content_angle",
                content=(
                    f"{biz_name}: {years}+ years in business.\n"
                    f"Angle: \"The {years}-year-old {prospect.source_input or 'trade'} business that just got its first automated lead system.\""
                ),
                source=biz_name,
            ))
            db.commit()

    except Exception as exc:
        db.rollback()
        _log(f"ERROR send_next_queued: {exc}")
    finally:
        db.close()


def job_process_followups() -> None:
    """SECTION 1 — Day-3 / Day-7 follow-ups. Counts toward daily limit."""
    if _state["paused"]:
        return

    from app import build_outreach_email

    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        due = (
            db.query(OutreachProspect)
            .filter(
                OutreachProspect.status.in_(["outreach_pending", "follow_up_pending"]),
                OutreachProspect.next_follow_up_at.isnot(None),
                OutreachProspect.next_follow_up_at <= now,
            )
            .all()
        )

        for p in due:
            if not _under_limit(db):
                break

            if p.follow_up_count >= 2:
                p.status = "follow_up_completed"
                p.next_follow_up_at = None
                db.add(p)
                db.commit()
                continue

            prefix = "Following up" if p.follow_up_count == 0 else "One last note"
            _, body = build_outreach_email(
                {
                    "business_name": p.business_name,
                    "website_description": p.business_description,
                    "industry": "service business",
                    "services_found": [],
                },
                p.source_input or p.source_url or "",
            )
            subject = f"{prefix} — {p.business_name or 'your business'}"

            ok = _send(p.email, subject, body, from_name="Tommy")

            p.last_contacted_at  = now
            p.last_email_subject = subject
            p.last_message       = body
            p.follow_up_count   += 1
            p.status             = "follow_up_pending"
            p.next_follow_up_at  = (
                p.created_at + timedelta(days=7) if p.follow_up_count == 1 else None
            )
            db.add(p)
            db.add(OutreachActivity(
                prospect_id=p.id, activity_type="follow_up_sent",
                subject=subject, body_preview=body[:180], status="sent",
            ))
            db.commit()
            _log(f"{'✓' if ok else '✗'} Follow-up #{p.follow_up_count} → {p.email}")

    except Exception as exc:
        db.rollback()
        _log(f"ERROR process_followups: {exc}")
    finally:
        db.close()


def job_check_replies() -> None:
    """
    SECTION 2 — Gmail IMAP scan.
    Matches replies to prospects, detects sentiment, sends Calendly / drafts, SMS alert.
    """
    if _state["paused"]:
        return

    user = os.getenv("SMTP_USER", "")
    pwd  = os.getenv("SMTP_PASSWORD", "")
    if not user or not pwd:
        return

    db = SessionLocal()
    try:
        with imaplib.IMAP4_SSL("imap.gmail.com", 993) as imap:
            imap.login(user, pwd)
            imap.select("INBOX")

            _, data = imap.search(None, "UNSEEN")
            ids = data[0].split() if data[0] else []

            if not ids:
                _state["last_reply_check_at"] = datetime.now(timezone.utc)
                return

            matched = 0
            for num in ids:
                try:
                    _, raw_data = imap.fetch(num, "(RFC822)")
                    msg         = email_lib.message_from_bytes(raw_data[0][1])
                    from_header = msg.get("From", "")
                    subject     = _decode_hdr(msg.get("Subject", ""))
                    body_text   = _body_text(msg)
                    sentiment   = _detect_sentiment(body_text)

                    prospect = _match_prospect(from_header, subject, db)
                    if not prospect:
                        continue

                    # Update prospect status
                    if prospect.status not in ("replied", "booked"):
                        new_status = "not_interested" if sentiment == "negative" else "replied"
                        prospect.status = new_status
                        db.add(prospect)
                        db.add(OutreachActivity(
                            prospect_id=prospect.id,
                            activity_type="reply_received",
                            subject=subject,
                            body_preview=body_text[:180],
                            status=new_status,
                        ))
                        db.commit()
                        _log(f"Reply [{sentiment}]: {prospect.business_name or from_header}")

                    _, from_email = parseaddr(from_header)

                    # Positive reply → send Calendly link immediately
                    if sentiment == "positive":
                        _send(
                            from_email,
                            f"Re: {subject}",
                            (
                                f"Hi,\n\n"
                                f"Thanks for getting back to me — great to hear!\n\n"
                                f"Here's my calendar to grab a quick call: {CALENDLY_LINK}\n\n"
                                f"Pick whatever works and I'll send over a confirmation.\n\n"
                                f"Tommy"
                            ),
                            from_name="Tommy",
                        )
                        _log(f"Calendly sent → {from_email}")

                    # Neutral reply → create draft for Tommy to approve
                    if sentiment == "neutral":
                        biz = prospect.business_name or "your business"
                        db.add(ClientDraft(
                            prospect_id=prospect.id,
                            draft_type="reply_response",
                            to_email=from_email,
                            subject=f"Re: {subject}",
                            body=(
                                f"Hi,\n\n"
                                f"Thanks for getting back to me.\n\n"
                                f"I help {biz} and businesses like yours capture more leads, "
                                f"respond faster, and follow up automatically.\n\n"
                                f"Would a quick 10-minute call make sense? "
                                f"Here's my calendar: {CALENDLY_LINK}\n\n"
                                f"Tommy"
                            ),
                        ))
                        db.commit()
                        _log(f"Draft created for neutral reply from {from_email}")

                    # Forward to Tommy with sentiment label
                    fwd_subj = f"[Reply • {sentiment.upper()}] {prospect.business_name or from_email}"
                    fwd_body = (
                        f"A prospect replied to your Duding outreach.\n\n"
                        f"Business:    {prospect.business_name or '—'}\n"
                        f"Website:     {prospect.website or '—'}\n"
                        f"Their email: {from_email}\n"
                        f"Sentiment:   {sentiment.upper()}\n"
                        f"Contacted:   {prospect.last_contacted_at.strftime('%b %d') if prospect.last_contacted_at else '—'}\n\n"
                        f"{'─' * 50}\n\n{body_text}\n\n"
                    )
                    if sentiment == "neutral":
                        fwd_body += "Draft reply created — approve it at /dashboard/drafts\n"
                    _send(TOMMY_EMAIL, fwd_subj, fwd_body, from_name="Duding")

                    # SECTION 9 — SMS alert
                    _send_sms(
                        f"Reply [{sentiment}]: {prospect.business_name or from_email}. Check duding@duding.ai"
                    )

                    imap.store(num, "+FLAGS", "\\Seen")
                    matched += 1

                except Exception as exc:
                    _log(f"Error on msg {num}: {exc}")

            _state["last_reply_check_at"] = datetime.now(timezone.utc)
            _log(
                f"Reply check: {matched} matched"
                if matched
                else f"Reply check: {len(ids)} unread, none matched"
            )

    except imaplib.IMAP4.error as exc:
        _log(f"IMAP error: {exc}")
    except Exception as exc:
        _log(f"ERROR check_replies: {exc}")
    finally:
        db.close()


def job_daily_summary() -> None:
    """SECTION 3 — 8am CST daily briefing email + SMS one-liner to Tommy."""
    db = SessionLocal()
    try:
        now     = datetime.now(timezone.utc)
        y_start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        y_end   = y_start + timedelta(days=1)
        w_start = now - timedelta(days=7)
        m_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        sent_yday = (
            db.query(OutreachActivity)
            .filter(
                OutreachActivity.activity_type.in_(["email_sent", "follow_up_sent"]),
                OutreachActivity.created_at >= y_start,
                OutreachActivity.created_at < y_end,
            ).count()
        )
        replies_yday = (
            db.query(OutreachActivity)
            .filter(
                OutreachActivity.activity_type == "reply_received",
                OutreachActivity.created_at >= y_start,
                OutreachActivity.created_at < y_end,
            ).count()
        )
        replies_week = (
            db.query(OutreachActivity)
            .filter(
                OutreachActivity.activity_type == "reply_received",
                OutreachActivity.created_at >= w_start,
            ).count()
        )

        total_prospects = db.query(OutreachProspect).count()
        hot_prospects   = (
            db.query(OutreachProspect)
            .filter(OutreachProspect.status.in_(["replied", "booked"]))
            .order_by(OutreachProspect.updated_at.desc())
            .limit(3)
            .all()
        )

        paid_builds = db.query(Build).filter(Build.deposit_paid == True).all()
        deposits_month = sum(
            1 for b in paid_builds
            if b.deposit_paid_at and (
                b.deposit_paid_at.replace(tzinfo=timezone.utc)
                if b.deposit_paid_at.tzinfo is None else b.deposit_paid_at
            ) >= m_start
        )
        revenue_total  = sum(b.deposit_amount_cents for b in paid_builds) / 100
        pipeline_value = sum(
            b.total_price_cents for b in paid_builds
            if b.status not in ("CANCELED", "LIVE")
        ) / 100

        due_soon = (
            db.query(OutreachProspect)
            .filter(
                OutreachProspect.next_follow_up_at.isnot(None),
                OutreachProspect.status.in_(["outreach_pending", "follow_up_pending"]),
            )
            .order_by(OutreachProspect.next_follow_up_at.asc())
            .limit(3)
            .all()
        )
        pending_drafts = (
            db.query(ClientDraft)
            .filter(ClientDraft.sent == False, ClientDraft.approved == False)
            .count()
        )

        hot_text = "\n".join(
            f"  {i+1}. {p.business_name or p.email}  [{p.status}]  {p.website or ''}"
            for i, p in enumerate(hot_prospects)
        ) or "  None yet."

        due_text = "\n".join(
            f"  • {p.business_name or p.email} — "
            f"{p.next_follow_up_at.strftime('%b %d') if p.next_follow_up_at else '?'}"
            for p in due_soon
        ) or "  None due."

        date_str = y_start.strftime("%A, %B %d")
        subject  = f"Duding Daily — {date_str}"
        body = (
            f"Good morning Tommy,\n\n"
            f"Business summary for {date_str}:\n\n"
            f"--- OUTREACH ---\n"
            f"  Emails sent yesterday:    {sent_yday} / {DAILY_SEND_LIMIT}\n"
            f"  Replies yesterday:        {replies_yday}\n"
            f"  Replies this week:        {replies_week}\n"
            f"  Total prospects:          {total_prospects}\n\n"
            f"--- REVENUE ---\n"
            f"  Deposits this month:      {deposits_month}\n"
            f"  Revenue to date:          ${revenue_total:,.0f}\n"
            f"  Pipeline value:           ${pipeline_value:,.0f}\n\n"
            f"--- HOT LEADS ---\n"
            f"{hot_text}\n\n"
            f"--- UP NEXT ---\n"
            f"  Follow-ups due:\n{due_text}\n"
            f"  Pending draft replies:    {pending_drafts}"
            f"{' — approve at /dashboard/drafts' if pending_drafts else ''}\n\n"
            f"Engine running. Daily limit: {DAILY_SEND_LIMIT} emails/day.\n\n"
            f"— Duding Engine"
        )

        ok = _send(TOMMY_EMAIL, subject, body, from_name="Duding")
        _state["last_summary_at"] = datetime.now(timezone.utc)
        _log(f"{'✓' if ok else '✗'} Daily summary → {TOMMY_EMAIL}")

        # SECTION 9 — SMS one-liner
        _send_sms(
            f"Duding {date_str[:6]}: {sent_yday} sent, {replies_yday} replies, "
            f"${revenue_total:,.0f} rev. {deposits_month} deposits this month."
        )

    except Exception as exc:
        _log(f"ERROR daily_summary: {exc}")
    finally:
        db.close()


def job_build_status_emails() -> None:
    """SECTIONS 5 — Day 3 / 7 / 10 build status emails to clients."""
    if _state["paused"]:
        return

    db = SessionLocal()
    try:
        now    = datetime.now(timezone.utc)
        builds = (
            db.query(Build)
            .filter(
                Build.deposit_paid == True,
                Build.status.notin_(["CANCELED"]),
            )
            .all()
        )

        for build in builds:
            paid_at = build.deposit_paid_at
            if not paid_at:
                continue
            if paid_at.tzinfo is None:
                paid_at = paid_at.replace(tzinfo=timezone.utc)
            days = (now - paid_at).days

            if days >= 3 and not build.day3_sent:
                subj, body = _build_day3_email(build)
                ok = _send(build.email, subj, body, from_name="Tommy")
                build.day3_sent = True
                db.add(build)
                db.commit()
                _log(f"{'✓' if ok else '✗'} Day-3 → {build.email}")

            if days >= 7 and not build.day7_sent:
                subj, body = _build_day7_email(build)
                ok = _send(build.email, subj, body, from_name="Tommy")
                build.day7_sent = True
                db.add(build)
                db.commit()
                _log(f"{'✓' if ok else '✗'} Day-7 preview → {build.email}")

            if days >= 10 and not build.day10_sent:
                subj, body = _build_day10_email(build)
                ok = _send(build.email, subj, body, from_name="Tommy")
                build.day10_sent  = True
                build.status      = "LIVE"
                build.completed_at = now
                db.add(build)
                db.commit()
                _log(f"{'✓' if ok else '✗'} Day-10 LIVE → {build.email}")

            if build.status == "LIVE" and not build.retainer_upsell_sent:
                subj, body = _retainer_upsell_email(build)
                ok = _send(build.email, subj, body, from_name="Tommy")
                build.retainer_upsell_sent = True
                db.add(build)
                db.commit()
                _log(f"{'✓' if ok else '✗'} Retainer upsell → {build.email}")
                _send_sms(f"Build LIVE: {build.business_name or build.email}")

        _state["last_build_check_at"] = now

    except Exception as exc:
        db.rollback()
        _log(f"ERROR build_status_emails: {exc}")
    finally:
        db.close()


def job_testimonial_requests() -> None:
    """SECTION 6 — Testimonial + referral request 7 days after LIVE."""
    if _state["paused"]:
        return

    db = SessionLocal()
    try:
        now       = datetime.now(timezone.utc)
        threshold = now - timedelta(days=7)
        for build in (
            db.query(Build)
            .filter(
                Build.status == "LIVE",
                Build.testimonial_sent == False,
                Build.completed_at.isnot(None),
                Build.completed_at <= threshold,
            )
            .all()
        ):
            subj, body = _testimonial_email(build)
            ok = _send(build.email, subj, body, from_name="Tommy")
            build.testimonial_sent = True
            db.add(build)
            db.commit()
            _log(f"{'✓' if ok else '✗'} Testimonial → {build.email}")

    except Exception as exc:
        db.rollback()
        _log(f"ERROR testimonial_requests: {exc}")
    finally:
        db.close()


def job_upsell_followups() -> None:
    """SECTION 7 — Upsell at 30 days, follow-up nudge at 37 days."""
    if _state["paused"]:
        return

    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        t30 = now - timedelta(days=30)
        t37 = now - timedelta(days=37)

        for build in (
            db.query(Build)
            .filter(
                Build.status == "LIVE",
                Build.upsell_sent == False,
                Build.completed_at.isnot(None),
                Build.completed_at <= t30,
            )
            .all()
        ):
            subj, body = _upsell_email(build, is_followup=False)
            ok = _send(build.email, subj, body, from_name="Tommy")
            build.upsell_sent = True
            db.add(build)
            db.commit()
            _log(f"{'✓' if ok else '✗'} Upsell (30d) → {build.email}")

        for build in (
            db.query(Build)
            .filter(
                Build.status == "LIVE",
                Build.upsell_sent == True,
                Build.upsell_day37_sent == False,
                Build.completed_at.isnot(None),
                Build.completed_at <= t37,
            )
            .all()
        ):
            subj, body = _upsell_email(build, is_followup=True)
            ok = _send(build.email, subj, body, from_name="Tommy")
            build.upsell_day37_sent = True
            db.add(build)
            db.commit()
            _log(f"{'✓' if ok else '✗'} Upsell follow-up (37d) → {build.email}")

    except Exception as exc:
        db.rollback()
        _log(f"ERROR upsell_followups: {exc}")
    finally:
        db.close()


def job_monday_content() -> None:
    """SECTION 8 — Monday morning: 5 video hooks + save to DB + email Tommy."""
    db = SessionLocal()
    try:
        now     = datetime.now(timezone.utc)
        w_start = now - timedelta(days=7)

        sent_week = (
            db.query(OutreachActivity)
            .filter(
                OutreachActivity.activity_type.in_(["email_sent", "follow_up_sent"]),
                OutreachActivity.created_at >= w_start,
            ).count()
        )
        reply_week = (
            db.query(OutreachActivity)
            .filter(
                OutreachActivity.activity_type == "reply_received",
                OutreachActivity.created_at >= w_start,
            ).count()
        )

        recent_inputs = (
            db.query(OutreachProspect.source_input)
            .filter(OutreachProspect.created_at >= w_start)
            .limit(50)
            .all()
        )
        trade_counts: Dict[str, int] = {}
        for (inp,) in recent_inputs:
            if inp:
                word = inp.split()[0]
                trade_counts[word] = trade_counts.get(word, 0) + 1
        top_trade = max(trade_counts, key=trade_counts.get) if trade_counts else "service"

        hooks = _gen_video_hooks(sent_week, reply_week, top_trade)
        for h in hooks:
            db.add(ContentIdea(idea_type="video_hook", content=h, source="weekly_stats"))
        db.commit()

        hooks_text = "\n\n".join(f"{i+1}. {h}" for i, h in enumerate(hooks))
        _send(
            TOMMY_EMAIL,
            f"[Content] 5 video hooks — week of {now.strftime('%b %d')}",
            (
                f"Good morning Tommy!\n\n"
                f"5 video hook ideas based on last week's outreach:\n\n"
                f"Stats: {sent_week} emails sent, {reply_week} replies, top trade: {top_trade}\n\n"
                f"{'─' * 50}\n\n{hooks_text}\n\n"
                f"Pick one, record a 30-second clip, post it. 🎬\n\n— Duding Engine"
            ),
            from_name="Duding",
        )
        _log(f"✓ Monday content hooks → {TOMMY_EMAIL}")

    except Exception as exc:
        db.rollback()
        _log(f"ERROR monday_content: {exc}")
    finally:
        db.close()


# ── SECTION 9 — Resend domain verification watcher ───────────────────────────

_domain_verified = False  # module-level flag; set True once confirmed


def job_check_resend_domain_verification() -> None:
    """Poll Resend every 15 min until duding.ai is verified, then notify Tommy and self-remove."""
    global _domain_verified
    if _domain_verified:
        return

    key = os.getenv("RESEND_API_KEY", "").strip()
    if not key or not RESEND_DOMAIN_ID:
        return

    import httpx
    try:
        r = httpx.get(
            f"https://api.resend.com/domains/{RESEND_DOMAIN_ID}",
            headers={"Authorization": f"Bearer {key}"},
            timeout=10,
        )
        if r.status_code != 200:
            _log(f"[domain-check] Resend API error {r.status_code}: {r.text[:120]}")
            return

        data = r.json()
        status = data.get("status", "unknown")
        records = data.get("records", [])

        verified_names = [rec.get("record", "") for rec in records if rec.get("status") == "verified"]
        pending_names  = [rec.get("record", "") for rec in records if rec.get("status") != "verified"]

        _log(f"[domain-check] status={status} ✓={verified_names} pending={pending_names}")

        sending_enabled = data.get("capabilities", {}).get("sending") == "enabled"
        if status == "verified" or sending_enabled:
            _domain_verified = True
            _log("[domain-check] ✓ duding.ai sending ENABLED — outreach emails will now deliver")
            _send(
                TOMMY_EMAIL,
                "duding.ai domain sending enabled — outreach is live",
                (
                    "Good news — the duding.ai domain can now send email via Resend.\n\n"
                    f"Domain status: {status}\n"
                    f"Sending capability: {data.get('capabilities', {}).get('sending', 'unknown')}\n"
                    f"Verified records: {', '.join(verified_names) or 'all'}\n"
                    f"Still pending: {', '.join(pending_names) or 'none'}\n\n"
                    "All outreach emails will now deliver from duding@duding.ai.\n\n"
                    "— Duding Engine"
                ),
                from_name="Duding",
            )
            if _scheduler and _scheduler.running:
                try:
                    _scheduler.remove_job("domain_verify_check")
                except Exception:
                    pass
        else:
            # Re-trigger verification so Resend re-checks DNS
            rv = httpx.post(
                f"https://api.resend.com/domains/{RESEND_DOMAIN_ID}/verify",
                headers={"Authorization": f"Bearer {key}"},
                timeout=10,
            )
            _log(f"[domain-check] re-verify triggered ({rv.status_code}) — still pending: {pending_names}")

    except Exception as exc:
        _log(f"[domain-check] ERROR: {exc}")


# ── CHKD client jobs ──────────────────────────────────────────────────────────

def job_chkd_daily() -> None:
    """CHKD client — Day-3 re-engagement and Day-7 streak emails (daily 8am UTC)."""
    from services.chkd import run_daily_checks
    try:
        counts = run_daily_checks()
        _log(f"[chkd] daily sweep: {counts}")
    except Exception as exc:
        _log(f"[chkd] ERROR: {exc}")
    # Also fire milestone Discord announcements (14 / 30 / 60 / 100 days)
    job_discord_milestones()


# ── CHKD Discord jobs ──────────────────────────────────────────────────────────

def job_discord_daily_checkin() -> None:
    """CHKD Discord — post daily check-in prompt to #check-in at 8am UTC."""
    from services.discord import post_to_channel_by_name
    from datetime import datetime, timezone
    now      = datetime.now(timezone.utc)
    date_str = now.strftime("%B %d")
    msg = (
        f"🌅 Day {date_str}. Time to check in.\n"
        f"What are you scoring today?\n"
        f"Drop your score: Name / Score / Streak"
    )
    ok = post_to_channel_by_name("check-in", msg)
    _log(f"{'✓' if ok else '✗'} Discord check-in posted")


def job_discord_leaderboard() -> None:
    """CHKD Discord — post top-5 streak leaderboard to #leaderboard every Monday 9am UTC."""
    from services.discord import post_to_channel_by_name
    from services.chkd import get_all_profiles, get_recent_days, _parse_date
    from datetime import datetime, timezone, timedelta

    profiles    = get_all_profiles()
    recent_days = get_recent_days(lookback=105)

    days_by_user: Dict[str, List[Dict]] = {}
    for row in recent_days:
        uid = row.get("user_id", "")
        if uid:
            days_by_user.setdefault(uid, []).append(row)

    def _streak(user_days: List[Dict]) -> int:
        today = datetime.now(timezone.utc).date()
        n = 0
        for i in range(105):
            check = today - timedelta(days=i)
            hit = any(
                _parse_date(r.get("date")) == check and (r.get("score") or 0) > 0
                for r in user_days
            )
            if hit:
                n += 1
            else:
                break
        return n

    board = []
    for p in profiles:
        uid  = p.get("id", "")
        name = p.get("full_name") or p.get("email", "")
        if not uid:
            continue
        s = _streak(days_by_user.get(uid, []))
        if s > 0:
            board.append((name, s))

    board.sort(key=lambda x: x[1], reverse=True)
    top5 = board[:5]

    if not top5:
        _log("[discord] leaderboard: no active streaks to post")
        return

    medals = ["1.", "2.", "3.", "4.", "5."]
    lines  = ["🏆 WEEKLY LEADERBOARD"]
    for i, (name, streak) in enumerate(top5):
        first = (name or "Anonymous").split()[0]
        flame = " 🔥" if i == 0 else ""
        lines.append(f"{medals[i]} {first} — {streak} day streak{flame}")
    lines += ["", "Keep pushing. See you next Monday."]

    post_to_channel_by_name("leaderboard", "\n".join(lines))
    _log(f"✓ Discord leaderboard posted — {len(top5)} entries")


def job_discord_milestones() -> None:
    """CHKD Discord — announce 14/30/60/100-day milestones to #streaks (called by job_chkd_daily)."""
    from services.discord import (
        post_message as _disc_post,
        get_channel_by_name as _disc_ch,
        MILESTONE_LENGTHS,
        MILESTONE_MESSAGES,
    )
    from services.chkd import get_all_profiles, get_recent_days, _parse_date
    from models.chkd_email import ChkdEmail
    from datetime import datetime, timezone, timedelta

    profiles    = get_all_profiles()
    recent_days = get_recent_days(lookback=105)

    days_by_user: Dict[str, List[Dict]] = {}
    for row in recent_days:
        uid = row.get("user_id", "")
        if uid:
            days_by_user.setdefault(uid, []).append(row)

    streaks_ch = _disc_ch("streaks")
    cid        = streaks_ch["id"] if streaks_ch else None
    if not cid:
        _log("[discord] #streaks channel not found — skipping milestones")
        return

    db = SessionLocal()
    try:
        today = datetime.now(timezone.utc).date()
        for p in profiles:
            uid  = p.get("id", "")
            name = p.get("full_name") or p.get("email", "")
            if not uid:
                continue
            user_days = days_by_user.get(uid, [])

            # Compute streak length
            streak_len = 0
            for i in range(105):
                check = today - timedelta(days=i)
                if any(_parse_date(r.get("date")) == check and (r.get("score") or 0) > 0
                       for r in user_days):
                    streak_len += 1
                else:
                    break

            for n in MILESTONE_LENGTHS:
                if n == 7:
                    continue  # day7_streak email flow handles this
                if streak_len < n:
                    continue
                email_type = f"discord_streak_{n}"
                already = db.query(ChkdEmail).filter(
                    ChkdEmail.user_id    == uid,
                    ChkdEmail.email_type == email_type,
                ).first()
                if already:
                    continue

                first = (name or "Someone").split()[0]
                _disc_post(f"🔥 **{first} just hit {n} days straight.**\n{MILESTONE_MESSAGES[n]}", cid)
                db.add(ChkdEmail(user_id=uid, email=p.get("email", ""), email_type=email_type))
                db.commit()
                _log(f"[discord] milestone {n}d → {first}")

    except Exception as exc:
        db.rollback()
        _log(f"[discord] ERROR job_discord_milestones: {exc}")
    finally:
        db.close()


def job_discord_member_poll() -> None:
    """CHKD Discord — check for new server members every 2 minutes, post welcome."""
    from services.discord import check_new_members
    try:
        welcomed = check_new_members()
        if welcomed:
            _log(f"[discord] Welcomed {welcomed} new member(s)")
    except Exception as exc:
        _log(f"[discord] ERROR member_poll: {exc}")


def job_social_intelligence() -> None:
    """CHKD Social Intelligence — scrape competitors + Claude analysis (Monday 9am UTC)."""
    from db import SessionLocal
    from models.client import Client
    from services.social_intelligence import run_weekly_intelligence

    db = SessionLocal()
    try:
        chkd = db.query(Client).filter(Client.domain == "getchkd.app").first()
        if not chkd:
            _log("[social_intel] CHKD client record not found — skipping")
            return
        client_id = chkd.id
    finally:
        db.close()

    try:
        result = run_weekly_intelligence(client_id)
        _log(f"[social_intel] Weekly report complete — {len(result.get('top_hooks', []))} hooks, "
             f"{len(result.get('content_ideas', []))} content ideas")
        # Chain content generation immediately after intel completes
        job_content_generation(intel_report=result, client_id_override=client_id)
    except Exception as exc:
        _log(f"[social_intel] ERROR: {exc}")


def job_content_generation(intel_report: dict = None, client_id_override: int = None) -> None:
    """CHKD Content Generation — render 10 Instagram graphics (Monday after social intel)."""
    from db import SessionLocal
    from models.client import Client
    from services.content_gen import run_weekly_content_gen

    client_id = client_id_override
    if not client_id:
        db = SessionLocal()
        try:
            chkd = db.query(Client).filter(Client.domain == "getchkd.app").first()
            if not chkd:
                _log("[content_gen] CHKD client not found — skipping")
                return
            client_id = chkd.id
        finally:
            db.close()

    try:
        count = run_weekly_content_gen(client_id, intel_report=intel_report)
        _log(f"[content_gen] {count} content pieces generated")
    except Exception as exc:
        _log(f"[content_gen] ERROR: {exc}")


# ── Public API ────────────────────────────────────────────────────────────────

def start_engine() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _log("Already running")
        return

    _scheduler = BackgroundScheduler(timezone="UTC")
    now = datetime.now(timezone.utc)

    # SECTION 1 — Outreach
    _scheduler.add_job(
        job_find_prospects, "interval", hours=3, id="find_prospects",
        next_run_time=now + timedelta(minutes=2),
        max_instances=1, misfire_grace_time=600,
    )
    _scheduler.add_job(
        job_send_next_queued, "interval", minutes=20, id="send_queued",
        next_run_time=now + timedelta(minutes=5),
        max_instances=1, misfire_grace_time=120,
    )
    _scheduler.add_job(
        job_process_followups, "interval", hours=1, id="process_followups",
        next_run_time=now + timedelta(minutes=10),
        max_instances=1, misfire_grace_time=600,
    )
    # SECTION 2 — Reply detection
    _scheduler.add_job(
        job_check_replies, "interval", minutes=30, id="check_replies",
        next_run_time=now + timedelta(minutes=3),
        max_instances=1, misfire_grace_time=180,
    )
    # SECTION 3 — Daily briefing 8am CST
    _scheduler.add_job(
        job_daily_summary,
        CronTrigger(hour=8, minute=0, timezone="America/Chicago"),
        id="daily_summary",
        max_instances=1, misfire_grace_time=3600,
    )
    # SECTIONS 4+5 — Build status every 2h
    _scheduler.add_job(
        job_build_status_emails, "interval", hours=2, id="build_status",
        next_run_time=now + timedelta(minutes=15),
        max_instances=1, misfire_grace_time=600,
    )
    # SECTION 6 — Testimonials daily 9am CST
    _scheduler.add_job(
        job_testimonial_requests,
        CronTrigger(hour=9, minute=0, timezone="America/Chicago"),
        id="testimonials",
        max_instances=1, misfire_grace_time=3600,
    )
    # SECTION 7 — Upsell daily 9:30am CST
    _scheduler.add_job(
        job_upsell_followups,
        CronTrigger(hour=9, minute=30, timezone="America/Chicago"),
        id="upsell",
        max_instances=1, misfire_grace_time=3600,
    )
    # SECTION 8 — Monday content 7am CST
    _scheduler.add_job(
        job_monday_content,
        CronTrigger(day_of_week="mon", hour=7, minute=0, timezone="America/Chicago"),
        id="monday_content",
        max_instances=1, misfire_grace_time=3600,
    )
    # SECTION 9 — Resend domain verification watcher (runs until verified, then self-removes)
    _scheduler.add_job(
        job_check_resend_domain_verification, "interval", minutes=15,
        id="domain_verify_check",
        next_run_time=now + timedelta(seconds=45),
        max_instances=1, misfire_grace_time=60,
    )
    # CHKD client — Day-3 re-engagement + Day-7 streak daily at 8am UTC
    _scheduler.add_job(
        job_chkd_daily,
        CronTrigger(hour=8, minute=0, timezone="UTC"),
        id="chkd_daily",
        max_instances=1, misfire_grace_time=3600,
    )
    # CHKD Social Intelligence — Monday 9am UTC
    _scheduler.add_job(
        job_social_intelligence,
        CronTrigger(day_of_week="mon", hour=9, minute=0, timezone="UTC"),
        id="social_intelligence",
        max_instances=1, misfire_grace_time=3600,
    )
    # CHKD Discord — daily check-in post 8am UTC
    _scheduler.add_job(
        job_discord_daily_checkin,
        CronTrigger(hour=8, minute=0, timezone="UTC"),
        id="discord_checkin",
        max_instances=1, misfire_grace_time=3600,
    )
    # CHKD Discord — weekly leaderboard Monday 9am UTC
    _scheduler.add_job(
        job_discord_leaderboard,
        CronTrigger(day_of_week="mon", hour=9, minute=0, timezone="UTC"),
        id="discord_leaderboard",
        max_instances=1, misfire_grace_time=3600,
    )
    # CHKD Discord — new member poll every 2 minutes
    _scheduler.add_job(
        job_discord_member_poll, "interval", minutes=2,
        id="discord_member_poll",
        next_run_time=now + timedelta(minutes=1),
        max_instances=1, misfire_grace_time=60,
    )

    _scheduler.start()
    _state["running"] = True
    _log(
        "Engine started — "
        "find@+2m, reply-check@+3m, send@+5m, followups@+10m, build-check@+15m, "
        "domain-verify@+45s (every 15m until verified), chkd-daily@08:00UTC, "
        "social-intel@Mon09:00UTC"
    )


def stop_engine() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _state["running"] = False
    _log("Engine stopped")


def set_paused(paused: bool) -> None:
    _state["paused"] = paused
    _log(f"Engine {'PAUSED' if paused else 'RESUMED'}")


def get_status() -> Dict[str, Any]:
    db = SessionLocal()
    try:
        today_sent = _today_sent_count(db)
        counts = {
            "queued":    db.query(OutreachProspect).filter(OutreachProspect.status == "queued").count(),
            "pending":   db.query(OutreachProspect).filter(OutreachProspect.status == "outreach_pending").count(),
            "follow_up": db.query(OutreachProspect).filter(OutreachProspect.status == "follow_up_pending").count(),
            "replied":   db.query(OutreachProspect).filter(OutreachProspect.status == "replied").count(),
            "booked":    db.query(OutreachProspect).filter(OutreachProspect.status == "booked").count(),
        }
        paid_builds = db.query(Build).filter(Build.deposit_paid == True).all()
        builds = {
            "installing":    db.query(Build).filter(Build.status == "INSTALLING").count(),
            "live":          db.query(Build).filter(Build.status == "LIVE").count(),
            "total_revenue": sum(b.deposit_amount_cents for b in paid_builds) / 100,
        }
        pending_drafts = (
            db.query(ClientDraft)
            .filter(ClientDraft.sent == False, ClientDraft.approved == False)
            .count()
        )

        next_runs: Dict[str, Optional[str]] = {}
        if _scheduler and _scheduler.running:
            for job in _scheduler.get_jobs():
                nrt = job.next_run_time
                next_runs[job.id] = nrt.strftime("%H:%M UTC") if nrt else None

        def _fmt(dt: Optional[datetime]) -> Optional[str]:
            return dt.strftime("%H:%M UTC") if dt else None

        return {
            "running":        _state["running"],
            "paused":         _state["paused"],
            "today_sent":     today_sent,
            "daily_limit":    DAILY_SEND_LIMIT,
            "counts":         counts,
            "builds":         builds,
            "pending_drafts": pending_drafts,
            "next_runs":      next_runs,
            "last_find_at":         _fmt(_state["last_find_at"]),
            "last_send_at":         _fmt(_state["last_send_at"]),
            "last_reply_check_at":  _fmt(_state["last_reply_check_at"]),
            "last_summary_at":      _fmt(_state["last_summary_at"]),
            "last_build_check_at":  _fmt(_state["last_build_check_at"]),
            "log": list(_log_ring)[-40:],
        }
    finally:
        db.close()
