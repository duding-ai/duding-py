from dotenv import load_dotenv

load_dotenv(override=True)

import uuid
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Set, Tuple, List
from io import StringIO
import csv
import os
import re
from urllib.parse import quote, urlparse

import pdfkit
import requests
import stripe
from bs4 import BeautifulSoup

from services.email import (
    send_email,
    send_html_email,
    send_email_with_attachment,
)

from fastapi import FastAPI, Request, Form, BackgroundTasks, Depends, HTTPException
from fastapi.responses import (
    HTMLResponse,
    RedirectResponse,
    StreamingResponse,
    JSONResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session

from site_scan import scan_site
from services.pricing import compute_price_and_timeline
from ddgs import DDGS

import business_profile as business_profile_module
from business_profile import build_profile_from_lead_row
from db import SessionLocal, engine, Base

from models.lead import Lead
from models.lead_event import LeadEvent
from models.business_settings import BusinessSettings
from models.build import Build
from models.outreach_prospect import OutreachProspect
from models.outreach_activity import OutreachActivity
from models.referral import Referral
from models.content_idea import ContentIdea
from models.client_draft import ClientDraft
from models.retainer_client import RetainerClient
from models.chkd_email import ChkdEmail
from models.client import Client
from models.social_intelligence_report import SocialIntelligenceReport
from models.content_piece import ContentPiece

from schemas import LeadCreate, LeadRead, LeadEventRead

# ---------------------------------------------------------------------
# BASIC CONFIG
# ---------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
app = FastAPI()


@app.get("/health")
async def health():
    return {"status": "ok"}


app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET", "CHANGE_ME_FOR_PROD"),
)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

Base.metadata.create_all(bind=engine)

# Custom Jinja2 filters
import json as _json_mod
templates.env.filters["fromjson"] = _json_mod.loads

# Inline column migrations — safe to re-run; IF NOT EXISTS is idempotent.
# Required because create_all() only creates missing tables, not missing columns.
if not engine.url.drivername.startswith("sqlite"):
    with engine.connect() as _conn:
        _conn.execute(__import__("sqlalchemy").text(
            "ALTER TABLE builds ADD COLUMN IF NOT EXISTS "
            "retainer_upsell_sent BOOLEAN NOT NULL DEFAULT false"
        ))
        _conn.commit()

CHKD_WEBHOOK_SECRET = os.getenv("CHKD_WEBHOOK_SECRET", "")


# ---------------------------------------------------------------------
# AUTONOMOUS OUTREACH ENGINE — lifecycle hooks
# ---------------------------------------------------------------------

@app.on_event("startup")
async def _engine_startup():
    from outreach_engine import start_engine
    start_engine()


@app.on_event("startup")
async def _seed_clients():
    """Seed CHKD as the first client if not already present."""
    db = SessionLocal()
    try:
        if not db.query(Client).filter(Client.domain == "getchkd.app").first():
            db.add(Client(
                name="CHKD",
                type="internal",
                status="active",
                domain="getchkd.app",
                dashboard_url="https://getchkd.app",
                notes="First Duding.ai client. Supabase backend, Netlify hosting.",
            ))
            db.commit()
    finally:
        db.close()


@app.on_event("shutdown")
async def _engine_shutdown():
    from outreach_engine import stop_engine
    stop_engine()


# ---------------------------------------------------------------------
# ADMIN LOGIN (env-driven)
# ---------------------------------------------------------------------

ADMIN_EMAIL = (os.getenv("ADMIN_EMAIL") or "duding@duding.ai").strip().lower()
ADMIN_PASSWORD = (os.getenv("ADMIN_PASSWORD") or "CHANGE_ME").strip()

print("[DEBUG] ADMIN_EMAIL =", repr(ADMIN_EMAIL))
print("[DEBUG] ADMIN_PASSWORD length =", len(ADMIN_PASSWORD))


# ---------------------------------------------------------------------
# EMAIL SETTINGS
# ---------------------------------------------------------------------

FROM_NAME = os.getenv("FROM_NAME", "Duding.ai")
ADMIN_NOTIFY_EMAIL = os.getenv("ADMIN_NOTIFY_EMAIL", ADMIN_EMAIL)
ADMIN_NAME = os.getenv("ADMIN_NAME", ADMIN_EMAIL.split("@")[0].capitalize())


# ---------------------------------------------------------------------
# PDFKIT / WKHTMLTOPDF CONFIG
# ---------------------------------------------------------------------

PDFKIT_CONFIG = None
try:
    wk = os.getenv(
        "WKHTMLTOPDF_PATH",
        r"C:\Program Files\wkhtmltopdf\bin\wkhtmltopdf.exe",
    )
    if wk and os.path.exists(wk):
        PDFKIT_CONFIG = pdfkit.configuration(wkhtmltopdf=wk)
        print("[pdf] Using wkhtmltopdf at", wk)
    else:
        print("[pdf] wkhtmltopdf not found at:", wk)
except Exception as exc:
    print("[pdf] Warning: could not configure wkhtmltopdf:", exc)
    PDFKIT_CONFIG = None


# ---------------------------------------------------------------------
# STRIPE CONFIG
# ---------------------------------------------------------------------

STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "").strip()
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
STRIPE_SUCCESS_URL = os.getenv("STRIPE_SUCCESS_URL", "").strip()
STRIPE_CANCEL_URL = os.getenv("STRIPE_CANCEL_URL", "").strip()

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY
else:
    print("[stripe] STRIPE_SECRET_KEY not set (deposit checkout will fail).")


# ---------------------------------------------------------------------
# LEAD SCORING (STEP 1) - legacy
# ---------------------------------------------------------------------


def _score_lead(
    *,
    budget: str,
    cadence: str,
    recent: str,
    role: str,
    website: str,
    link: str,
    email: str,
) -> Tuple[int, str, str]:
    score = 0
    notes: List[str] = []

    b = (budget or "").strip()
    c = (cadence or "").strip()
    r = (recent or "").strip()
    ro = (role or "").strip()
    w = (website or "").strip()
    l = (link or "").strip()
    e = (email or "").strip()

    # Budget (0-40)
    if "$100k" in b:
        score += 40
        notes.append("Budget: 100k+")
    elif "$20k" in b:
        score += 30
        notes.append("Budget: 20k–100k")
    elif "$5k" in b:
        score += 20
        notes.append("Budget: 5k–20k")
    elif "$0" in b:
        score += 10
        notes.append("Budget: 0–5k")
    else:
        notes.append("Budget: unknown")

    # Posting cadence (0-15)
    if "2–3" in c or "2-3" in c:
        score += 15
        notes.append("Cadence: 2–3/week")
    elif "Weekly" in c:
        score += 10
        notes.append("Cadence: weekly")
    elif "Less" in c:
        score += 5
        notes.append("Cadence: low")
    else:
        notes.append("Cadence: unknown")

    # Recency (0-15)
    if "Within 1 week" in r:
        score += 15
        notes.append("Recent: active")
    elif "Within 2–6 weeks" in r or "Within 2-6 weeks" in r:
        score += 8
        notes.append("Recent: semi-active")
    elif "6+" in r:
        score += 2
        notes.append("Recent: dormant")
    else:
        notes.append("Recent: unknown")

    # Role (0-10)
    if "Owner" in ro or "Founder" in ro or "CEO" in ro:
        score += 10
        notes.append("Role: decision maker")
    elif ro:
        score += 5
        notes.append("Role: non-owner")
    else:
        notes.append("Role: unknown")

    # Signals (0-20)
    if e:
        score += 5
        notes.append("Email: provided")
    else:
        notes.append("Email: missing")

    if w:
        score += 10
        notes.append("Website: provided")
    else:
        notes.append("Website: missing")

    if l:
        score += 5
        notes.append("Content link: provided")
    else:
        notes.append("Content link: missing")

    score = max(0, min(100, score))

    if score >= 75:
        tier = "hot"
    elif score >= 45:
        tier = "warm"
    else:
        tier = "cold"

    return score, tier, " | ".join(notes)


# ---------------------------------------------------------------------
# DB HELPERS
# ---------------------------------------------------------------------


def _lead_to_dict(lead: "Lead") -> dict:
    """Convert a Lead ORM object to a plain dict so templates use row['key'] syntax."""
    return {
        "id": lead.id,
        "created_at": lead.created_at,
        "created": lead.created_at,
        "name": lead.name,
        "email": lead.email,
        "business": lead.business,
        "website": lead.website,
        "budget": lead.budget,
        "link": lead.link,
        "role": lead.role,
        "cadence": lead.cadence,
        "recent": lead.recent,
        "status": lead.status or "new",
        "run_count": lead.run_count or 0,
        "last_run": lead.last_run,
        "score": lead.score or 0,
        "tier": lead.tier or "cold",
        "score_notes": lead.score_notes,
    }


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_business_settings(db: Session) -> BusinessSettings | None:
    return db.query(BusinessSettings).order_by(BusinessSettings.id.asc()).first()


def require_session(request: Request) -> Optional[int]:
    user_id = request.session.get("user_id")
    try:
        return int(user_id)
    except Exception:
        return None


def parse_created_to_utc(created) -> Optional[datetime]:
    if not created:
        return None

    if isinstance(created, datetime):
        if created.tzinfo is None:
            return created.replace(tzinfo=timezone.utc)
        return created.astimezone(timezone.utc)

    s = str(created).strip()
    if not s:
        return None

    if s.endswith("Z"):
        s = s.replace("Z", "+00:00")

    try:
        dt = datetime.fromisoformat(s)
    except Exception:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    return dt


# ---------------------------------------------------------------------
# EMAIL HELPERS
# ---------------------------------------------------------------------


# send_email / send_html_email / send_email_with_attachment are imported from
# services.email (Resend API) at the top of this file.


# ---------------------------------------------------------------------
# OUTREACH HELPERS
# ---------------------------------------------------------------------


def resolve_business_url(target: str) -> str:
    value = (target or "").strip()
    if not value:
        return ""
    if value.startswith(("http://", "https://")):
        return value

    try:
        url = f"https://duckduckgo.com/html/?q={quote(value)}"
        response = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        for link in soup.select("a.result__a"):
            href = link.get("href")
            if href:
                return href
    except Exception as exc:
        print("[outreach] search fallback failed:", exc)
    return ""


def scrape_business_profile(target: str) -> dict:
    resolved_url = resolve_business_url(target)
    if not resolved_url:
        return {
            "scraped": False,
            "error": "No resolvable URL",
            "business_name": (target or "").strip() or "Unknown business",
        }

    try:
        profile = business_profile_module._scrape_website(resolved_url)
        profile.setdefault(
            "business_name", (target or "").strip() or "Unknown business"
        )
        return profile
    except Exception as exc:
        return {
            "scraped": False,
            "error": str(exc),
            "business_name": target or "Unknown business",
        }


def _clean_business_name(raw: str) -> str:
    """Strip URL cruft and page-title suffixes so the name reads cleanly in a subject line."""
    name = (raw or "").strip()
    # URL → extract bare domain label (e.g. "best-hvac-austin.com" → "Best Hvac Austin")
    if name.startswith(("http://", "https://", "www.")):
        try:
            domain = urlparse(name if "://" in name else "https://" + name).netloc
            domain = domain.replace("www.", "")
            name = domain.split(".")[0].replace("-", " ").replace("_", " ").title()
        except Exception:
            pass
    else:
        # Page title often has " | tagline" or " - tagline" suffix — keep only the first part
        for sep in (" | ", " — ", " - ", " – "):
            if sep in name:
                name = name.split(sep)[0].strip()
                break
    return name or "your team"


def build_outreach_email(profile: dict, target: str) -> Tuple[str, str]:
    raw_name      = profile.get("business_name") or profile.get("website_title") or target
    business_name = _clean_business_name(raw_name)
    industry      = profile.get("industry") or ""
    services      = profile.get("services_found") or []
    scraped       = bool(profile.get("scraped"))
    phones        = (profile.get("phones_found") or []) if scraped else None
    has_form      = bool(profile.get("forms_found")) if scraped else None
    socials       = profile.get("social_links") or []

    # Choose opener based on real signals — never quote their tagline back at them.
    # Priority: site-specific observations → trade pain point → generic.
    if scraped and not has_form and not phones:
        opener = (
            "Took a look at your site and couldn't find a way for someone to request a quote "
            "or leave their number. Most leads won't reach out cold — they scan the site, "
            "don't see an easy entry point, and move on to the next result."
        )
    elif scraped and not has_form and phones:
        opener = (
            "Looked at your site — the only way to reach you is to call. "
            "A lot of people won't dial cold; they'll bounce and book whoever has a form. "
            "A short quote request with an instant text-back usually recovers those leads."
        )
    elif industry == "HVAC":
        opener = (
            "Most HVAC companies lose the summer spike leads to whoever texts back first. "
            "A homeowner calls 3 companies and books the one that responds within 5 minutes "
            "— the others never hear back."
        )
    elif industry == "Roofing":
        opener = (
            "After a storm rolls through, there's usually a 10–14 day window where every "
            "homeowner in the area is actively looking. Most roofing companies miss half those "
            "leads to slow follow-up — they respond the next day and the job is already gone."
        )
    elif industry == "Plumbing":
        opener = (
            "Emergency plumbing calls go to whoever texts back first, not whoever has the best "
            "reviews. Most plumbing businesses I talk to are losing 2–3 jobs a week to that "
            "5-minute response gap."
        )
    elif industry == "Electrical":
        opener = (
            "Electrical leads almost always call 2–3 contractors and hire the first to respond. "
            "The quote barely matters if you're third to call back."
        )
    elif industry in ("Cleaning", "Landscaping", "Pest Control"):
        opener = (
            "Recurring-service businesses win on follow-up speed more than price. "
            "The customer who requests a cleaning quote on Monday has usually booked someone "
            "by Tuesday morning — whoever followed up first."
        )
    elif scraped and socials and not has_form:
        platform = next(
            (s for s in ("Instagram", "Facebook", "TikTok")
             if s.lower() in " ".join(socials).lower()),
            "social media",
        )
        opener = (
            f"Noticed you're active on {platform}. "
            "The content is doing the awareness work — but there's usually a gap between "
            "someone seeing a post and actually becoming a lead. "
            "A short intake form with an instant text-back closes it."
        )
    else:
        svc = services[0].lower() if services else "inbound"
        opener = (
            f"Most service businesses lose 30–40% of {svc} leads to slow follow-up — "
            "someone calls or submits a form, doesn't hear back within the hour, "
            "and books the competitor who responded first."
        )

    service_str = services[0].lower() if services else "service"
    lever = f"turn more {service_str} inquiries into booked jobs"

    subject = f"Quick question — {business_name}"
    body = (
        f"Hi there,\n\n"
        f"{opener}\n\n"
        f"I build lead intake systems for service businesses — the kind that text a new lead "
        f"back within 60 seconds, run a follow-up sequence for 7 days, and make sure no call "
        f"goes unanswered. Built to {lever}.\n\n"
        f"Worth a quick 10-minute call this week to see if it makes sense for {business_name}?\n\n"
        f"Tommy"
    )
    return subject, body


# ── Email priority helpers ────────────────────────────────────────────────────

_GENERIC_LOCAL = frozenset({
    "info", "contact", "hello", "hi", "support", "sales", "team",
    "office", "mail", "noreply", "no-reply", "enquiries", "enquiry",
    "billing", "accounts", "staff", "webmaster", "help", "service",
    "services", "general", "company", "business", "care", "news",
    "media", "pr", "marketing", "reservations", "feedback", "jobs",
    "careers", "hr", "legal", "privacy", "security", "customerservice",
    "customer", "customercare", "cs", "inquiry", "inquiries",
})
_OWNER_LOCAL = frozenset({
    "owner", "founder", "ceo", "president", "principal", "admin",
    "director", "manager", "partner",
})
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


def _email_priority(email: str) -> int:
    """0=owner/founder, 1=named person, 2=generic. Lower = better."""
    local = email.split("@")[0].lower()
    if local in _OWNER_LOCAL:
        return 0
    if local in _GENERIC_LOCAL:
        return 2
    if re.search(r"[a-zA-Z]", local) and len(local) >= 2:
        return 1
    return 2


def _extract_domain_emails(html: str, base_domain: str) -> List[str]:
    """Pull emails that belong to base_domain from an HTML page."""
    soup = BeautifulSoup(html, "lxml")
    emails: List[str] = []
    seen: Set[str] = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().startswith("mailto:"):
            raw = href[7:].split("?")[0].strip().lower().rstrip(".")
            if "@" in raw and base_domain in raw and raw not in seen:
                seen.add(raw)
                emails.append(raw)

    for m in _EMAIL_RE.finditer(soup.get_text(" ")):
        raw = m.group(0).lower().rstrip(".")
        if base_domain in raw and raw not in seen:
            seen.add(raw)
            emails.append(raw)

    return emails


def _scrape_contact_email(url: str) -> Tuple[str, str, str]:
    """
    Fetch homepage + About/Contact pages and return the best decision-maker email.
    Returns (email, quality, note) — quality is 'direct' or 'generic'.
    Priority: owner/founder/ceo > named person (john@co.com) > generic (info@, contact@).
    """
    try:
        parsed = urlparse(url if "://" in url else "https://" + url)
        domain = parsed.netloc.lower().replace("www.", "")
        base = f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        domain = _prospect_domain(url)
        base = f"https://{domain}"

    if not domain:
        return "info@example.com", "generic", "generic email — verify before sending"

    fallback = f"info@{domain}"
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    all_emails: List[str] = []

    for page in [url, f"{base}/about", f"{base}/about-us",
                 f"{base}/contact", f"{base}/contact-us"]:
        try:
            r = requests.get(page, headers={"User-Agent": ua}, timeout=8,
                             allow_redirects=True)
            if r.status_code == 200:
                all_emails.extend(_extract_domain_emails(r.text, domain))
        except Exception:
            continue

    if not all_emails:
        return fallback, "generic", "generic email — verify before sending"

    seen: Set[str] = set()
    deduped = [e for e in all_emails if not (e in seen or seen.add(e))]
    deduped.sort(key=_email_priority)

    best = deduped[0]
    quality = "direct" if _email_priority(best) < 2 else "generic"
    note = "" if quality == "direct" else "generic email — verify before sending"
    return best, quality, note


def _get_outreach_email_for_target(target: str, resolved_url: str) -> Tuple[str, str, str]:
    """Returns (email, quality, note). Scrapes About/Contact pages for real owner emails."""
    url = resolved_url or target
    if not url:
        return "info@duding.ai", "generic", "generic email — verify before sending"
    return _scrape_contact_email(url)


def _process_due_followups(db: Session) -> int:
    now = datetime.now(timezone.utc)
    prospects = (
        db.query(OutreachProspect)
        .filter(OutreachProspect.status.in_(["outreach_pending", "follow_up_pending"]))
        .filter(OutreachProspect.next_follow_up_at.isnot(None))
        .filter(OutreachProspect.next_follow_up_at <= now)
        .all()
    )

    for prospect in prospects:
        if prospect.follow_up_count >= 2:
            prospect.status = "follow_up_completed"
            db.add(prospect)
            continue

        if prospect.follow_up_count == 0:
            subject, body = build_outreach_email(
                {
                    "business_name": prospect.business_name,
                    "website_description": prospect.business_description,
                    "industry": "service business",
                    "services_found": [],
                },
                prospect.source_input or prospect.source_url or "",
            )
            subject = f"Following up on {prospect.business_name or 'your business'}"
        else:
            subject, body = build_outreach_email(
                {
                    "business_name": prospect.business_name,
                    "website_description": prospect.business_description,
                    "industry": "service business",
                    "services_found": [],
                },
                prospect.source_input or prospect.source_url or "",
            )
            subject = f"One more idea for {prospect.business_name or 'your business'}"

        send_email(prospect.email, subject, body)
        prospect.last_contacted_at = datetime.now(timezone.utc)
        prospect.last_email_subject = subject
        prospect.last_message = body
        prospect.follow_up_count += 1
        prospect.status = "follow_up_pending"
        prospect.next_follow_up_at = (
            prospect.created_at + timedelta(days=7)
            if prospect.follow_up_count == 1
            else None
        )
        db.add(prospect)
        db.add(
            OutreachActivity(
                prospect_id=prospect.id,
                activity_type="follow_up_sent",
                subject=subject,
                body_preview=body[:180],
                status="sent",
            )
        )

    db.commit()
    return len(prospects)


# ---------------------------------------------------------------------
# PROSPECT FINDER
# ---------------------------------------------------------------------

# Domains we never want to outreach — directories, aggregators, franchises, big tech
FINDER_BLOCKLIST: Set[str] = {
    # Aggregators & lead-gen directories
    "yelp.com", "angi.com", "angieslist.com", "homeadvisor.com",
    "thumbtack.com", "houzz.com", "bark.com", "porch.com",
    "yellowpages.com", "whitepages.com", "superpages.com",
    "mapquest.com", "bbb.org", "manta.com", "citysearch.com",
    "craftjack.com", "improvenet.com", "fixr.com", "homeguide.com",
    "networx.com", "servicemagic.com", "buildazoom.com", "houselogic.com",
    "contractormag.com", "prostoolkid.com", "thebalancemoney.com",
    # Social / search / big tech
    "google.com", "facebook.com", "instagram.com", "linkedin.com",
    "twitter.com", "x.com", "youtube.com", "tiktok.com", "pinterest.com",
    "bing.com", "yahoo.com", "duckduckgo.com",
    # Job sites
    "indeed.com", "glassdoor.com", "ziprecruiter.com",
    "monster.com", "careerbuilder.com", "simplyhired.com",
    # News / info / wiki
    "entrepreneur.com", "forbes.com", "businessinsider.com",
    "nytimes.com", "wsj.com", "usatoday.com",
    "wikihow.com", "wikipedia.org", "wikimedia.org",
    "quora.com", "reddit.com", "nextdoor.com",
    # Big national franchises (not local owner-operated)
    "rotorooter.com", "rescue-rooter.com",
    "onehourheat.com", "servicemaster.com", "stanley-steemer.com",
    "molly-maid.com", "terminix.com", "orkin.com",
    "misterplumber.com", "mrelectric.com", "belron.com",
    # Platforms / SaaS
    "servicetitan.com", "housecallpro.com", "jobber.com",
    "amazon.com",
    # Gov
    "bls.gov", "usa.gov", "ftc.gov", "sba.gov", "census.gov",
}

# Additional single-domain blocks (supplementing the set above)
FINDER_BLOCKLIST.update({
    # News / local media
    "statesman.com", "dallasnews.com", "chron.com", "azcentral.com",
    # More directories / aggregators
    "dexknows.com", "chaosads.com", "expertise.com", "proslist.com",
    "cylex.us.com", "merchantcircle.com", "mapquest.com",
    "roofingcontractors.us.com", "contractordirectory.com",
    "meetaplumber.com", "findaplumber.com", "callcontractor.com",
    # Hosting / deployment platforms
    "vercel.app", "netlify.app", "github.io", "pages.dev",
    "squarespace.com", "wix.com", "weebly.com",
    # Manufacturers / suppliers
    "polyglass.us", "certainteed.com", "gaf.com", "owenscorning.com",
    # National franchises (additions)
    "mrrooter.com", "mr-rooter.com", "rescuerooter.com",
    "oneguard.com", "serviceexperts.com", "airsolutionsfl.com",
})

# URL-fragment keywords that signal a directory page, not a company homepage
FINDER_BLOCKLIST_KW: List[str] = [
    "yelp", "angi", "homeadvisor", "thumbtack", "yellowpages",
    "directory", "-listings", "/listings", "reviews", "find-a-",
    "locator", "nearme", "near-me", "-quotes", "getapp", "advisor",
    "chaosads", "prolist", "dexknows", "expertise",
    "islanderofficialstore", "best-of", "/best-",
]


def _prospect_domain(url: str) -> str:
    try:
        netloc = urlparse(url if "://" in url else "https://" + url).netloc.lower()
        return netloc.replace("www.", "")
    except Exception:
        return ""


def _is_blocked(url: str) -> bool:
    domain = _prospect_domain(url)
    if not domain:
        return True
    root = ".".join(domain.split(".")[-2:]) if "." in domain else domain
    if domain in FINDER_BLOCKLIST or root in FINDER_BLOCKLIST:
        return True
    if any(kw in url.lower() for kw in FINDER_BLOCKLIST_KW):
        return True
    # Deep paths or article-style slugs are directory listings / blog posts, not homepages
    try:
        path_parts = [p for p in urlparse(url).path.split("/") if p]
        if len(path_parts) > 1:
            return True
        # Single long slug with many hyphens is almost always an article
        if path_parts and len(path_parts[0].split("-")) > 6:
            return True
    except Exception:
        pass
    return False


def find_business_prospects(search_term: str, max_results: int = 8) -> List[str]:
    """Search DuckDuckGo via ddgs and return real business website URLs."""
    found: List[str] = []
    seen: Set[str] = set()
    try:
        # Fetch more raw results than we need so filtering has headroom
        raw = list(DDGS().text(search_term, max_results=max_results * 3))
        for r in raw:
            href = (r.get("href") or "").strip()
            if not href.startswith("http"):
                continue
            domain = _prospect_domain(href)
            if not domain or domain in seen or _is_blocked(href):
                continue
            seen.add(domain)
            found.append(href)
            if len(found) >= max_results:
                break
    except Exception as exc:
        print(f"[finder] '{search_term}': {exc}")
    return found


# In-memory job tracker — survives restarts only if process stays up (fine for this use case)
_find_jobs: dict = {}


def _run_find_and_outreach(job_id: str, search_terms: List[str], max_per_term: int) -> None:
    """Background worker: find prospects → scrape → email → schedule follow-ups."""
    job = _find_jobs[job_id]
    db = SessionLocal()
    try:
        # Load existing prospect domains so we never email the same site twice
        existing_domains: Set[str] = set()
        for (w,) in db.query(OutreachProspect.website).filter(OutreachProspect.website.isnot(None)).all():
            d = _prospect_domain(w or "")
            if d:
                existing_domains.add(d)

        # ── Phase 1: gather URLs ──────────────────────────────────────────
        all_urls: List[str] = []
        seen_domains: Set[str] = set(existing_domains)

        for term in search_terms:
            job["log"].append(f"Searching: {term!r}…")
            urls = find_business_prospects(term, max_per_term)
            new_urls = []
            for url in urls:
                d = _prospect_domain(url)
                if d and d not in seen_domains:
                    seen_domains.add(d)
                    new_urls.append(url)
            all_urls.extend(new_urls)
            skipped = len(urls) - len(new_urls)
            job["log"].append(
                f"  → {len(new_urls)} new"
                + (f", {skipped} duplicate{'s' if skipped != 1 else ''} skipped" if skipped else "")
            )
            time.sleep(2)  # polite gap between DDG requests

        job["total"] = len(all_urls)
        if not all_urls:
            job["status"] = "done"
            job["log"].append("No new prospects found for these search terms.")
            return

        job["log"].append(f"Starting outreach for {len(all_urls)} prospects…")

        # ── Phase 2: scrape + email each ─────────────────────────────────
        for url in all_urls:
            try:
                profile = scrape_business_profile(url)
                resolved_url = profile.get("final_url") or url
                to_email, email_quality, email_note = _get_outreach_email_for_target(url, resolved_url)
                subject, body = build_outreach_email(profile, url)
                biz_name = _clean_business_name(
                    profile.get("business_name") or profile.get("website_title") or url
                )
                is_generic = email_quality == "generic"

                prospect = OutreachProspect(
                    source_input=url,
                    source_url=resolved_url or None,
                    business_name=biz_name or None,
                    email=to_email,
                    email_quality=email_quality,
                    email_note=email_note,
                    website=profile.get("final_url") or resolved_url or None,
                    business_description=(
                        profile.get("website_description")
                        or profile.get("industry")
                        or "service business"
                    )[:1000],
                    lever=subject,
                    status="pending_review" if is_generic else "outreach_pending",
                    next_follow_up_at=datetime.now(timezone.utc) + timedelta(days=3),
                    last_contacted_at=datetime.now(timezone.utc) if not is_generic else None,
                    last_email_subject=subject,
                    last_message=body,
                )
                db.add(prospect)
                db.flush()

                if not is_generic:
                    send_email(prospect.email, subject, body)
                    for act_type, act_subj, act_preview, act_status in [
                        ("email_sent", subject, body[:180], "sent"),
                        ("follow_up_scheduled", "Day 3 follow-up scheduled", "Follow-up scheduled for day 3.", "scheduled"),
                        ("follow_up_scheduled", "Day 7 follow-up scheduled", "Follow-up scheduled for day 7.", "scheduled"),
                    ]:
                        db.add(OutreachActivity(
                            prospect_id=prospect.id,
                            activity_type=act_type,
                            subject=act_subj,
                            body_preview=act_preview,
                            status=act_status,
                        ))
                    db.commit()
                    job["sent"] += 1
                    job["log"].append(f"✓ {biz_name}  →  {to_email}")
                else:
                    db.commit()
                    job["skipped"] += 1
                    job["log"].append(f"✋ {biz_name}  →  {to_email} [generic — held for review]")

            except Exception as exc:
                db.rollback()
                job["skipped"] += 1
                job["log"].append(f"✗ {url}  —  {exc}")

            job["processed"] += 1

        job["status"] = "done"
        job["log"].append(
            f"Done — {job['sent']} sent, "
            f"{job['skipped']} held/skipped."
        )

    except Exception as exc:
        job["status"] = "error"
        job["log"].append(f"Fatal error: {exc}")
        print(f"[finder] job {job_id} crashed:", exc)
    finally:
        db.close()


# ---------------------------------------------------------------------
# PUBLIC ROUTES
# ---------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def landing(
    request: Request, thanks: int | None = None, error: int | None = None
):
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "thanks": bool(thanks), "error": bool(error)},
    )


# ---------------------------------------------------------------------
# BUILDER ROUTES (Platform Flow) -> duding.db builds table
# ---------------------------------------------------------------------


@app.get("/builder", response_class=HTMLResponse)
async def builder_get(request: Request):
    return templates.TemplateResponse(
        "builder.html", {"request": request, "error": None}
    )


@app.post("/builder")
async def builder_post(
    request: Request,
    contact_name: str = Form(...),
    email: str = Form(...),
    business_name: str = Form(...),
    business_type: str = Form(...),
    lead_volume_tier: str = Form(...),
    stripe_confirmed: str = Form(...),  # expects "yes" or "no"
    package_tier: str = Form(...),
    db: Session = Depends(get_db),
):
    contact_name = (contact_name or "").strip()
    email_clean = (email or "").strip().lower()
    business_name = (business_name or "").strip()
    business_type = (business_type or "").strip()
    lead_volume_tier = (lead_volume_tier or "").strip()
    package_tier = (package_tier or "").strip().lower()
    stripe_ok = (stripe_confirmed or "").strip().lower() == "yes"

    if not stripe_ok:
        return templates.TemplateResponse(
            "builder.html",
            {
                "request": request,
                "error": "Stripe is required at launch. Select 'Yes' to continue.",
            },
            status_code=400,
        )

    if lead_volume_tier == "61_plus":
        return templates.TemplateResponse(
            "builder.html",
            {"request": request, "error": "61+ leads/week is not supported at launch."},
            status_code=400,
        )

    try:
        total_cents, timeline_days = compute_price_and_timeline(
            package_tier, lead_volume_tier
        )
    except Exception as exc:
        return templates.TemplateResponse(
            "builder.html",
            {"request": request, "error": f"Pricing error: {exc}"},
            status_code=400,
        )

    build = Build(
        build_id=str(uuid.uuid4()),
        contact_name=contact_name,
        email=email_clean,
        business_name=business_name,
        business_type=business_type,
        lead_volume_tier=lead_volume_tier,
        stripe_confirmed=True,
        package_tier=package_tier,
        total_price_cents=int(total_cents),
        timeline_days=int(timeline_days),
        deposit_amount_cents=50000,
        deposit_paid=False,
        status="CONFIGURED",
    )

    db.add(build)
    db.commit()
    db.refresh(build)

    return RedirectResponse(url=f"/builder/summary/{build.build_id}", status_code=303)


@app.get("/builder/summary/{build_id}", response_class=HTMLResponse)
async def builder_summary(
    request: Request, build_id: str, db: Session = Depends(get_db)
):
    build = db.query(Build).filter(Build.build_id == build_id).first()
    if not build:
        return RedirectResponse(url="/builder", status_code=303)

    total = f"${build.total_price_cents / 100:,.0f}"
    deposit = f"${build.deposit_amount_cents / 100:,.0f}"

    return templates.TemplateResponse(
        "builder_summary.html",
        {"request": request, "build": build, "total": total, "deposit": deposit},
    )


# -----------------------
# STRIPE: CREATE CHECKOUT
# -----------------------
@app.post("/builder/deposit/{build_id}")
async def builder_deposit_start(
    request: Request,
    build_id: str,
    db: Session = Depends(get_db),
):
    if not STRIPE_SECRET_KEY:
        raise HTTPException(
            status_code=500,
            detail="Stripe is not configured (missing STRIPE_SECRET_KEY).",
        )

    build = db.query(Build).filter(Build.build_id == build_id).first()
    if not build:
        raise HTTPException(status_code=404, detail="Build not found")

    if build.deposit_paid:
        return RedirectResponse(
            url=f"/builder/summary/{build.build_id}", status_code=303
        )

    base_url = str(request.base_url).rstrip("/")

    success_url = STRIPE_SUCCESS_URL or str(
        request.url_for(
            "builder_summary", build_id=build.build_id
        ).include_query_params(paid=1)
    )
    cancel_url = STRIPE_CANCEL_URL or str(
        request.url_for(
            "builder_summary", build_id=build.build_id
        ).include_query_params(canceled=1)
    )

    # Create Checkout Session for the deposit
    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            payment_method_types=["card"],
            customer_email=build.email,
            line_items=[
                {
                    "price_data": {
                        "currency": "usd",
                        "unit_amount": int(build.deposit_amount_cents),
                        "product_data": {
                            "name": "Duding Install Deposit",
                            "description": f"Deposit for build {build.build_id}",
                        },
                    },
                    "quantity": 1,
                }
            ],
            metadata={
                "build_id": build.build_id,
            },
            success_url=success_url,
            cancel_url=cancel_url,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Stripe error: {exc}")

    # Store session id (so we can debug / reconcile)
    build.deposit_payment_intent_id = session.id
    db.add(build)
    db.commit()

    return RedirectResponse(url=session.url, status_code=303)


# -----------------------
# STRIPE: WEBHOOK HANDLER
# -----------------------
@app.post("/stripe/webhook")
async def stripe_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail="Missing STRIPE_WEBHOOK_SECRET")

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=sig_header,
            secret=STRIPE_WEBHOOK_SECRET,
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        payment_status = session.get("payment_status")
        build_id = (session.get("metadata") or {}).get("build_id")

        if payment_status == "paid" and build_id:
            build = db.query(Build).filter(Build.build_id == build_id).first()
            if build and not build.deposit_paid:
                build.deposit_paid = True
                build.deposit_paid_at = datetime.now(timezone.utc)
                build.status = "DEPOSIT_PAID"
                build.deposit_payment_intent_id = (
                    session.get("payment_intent") or session.get("id")
                )
                db.add(build)
                db.commit()

                # SECTION 4 — trigger onboarding in background
                build_int_id = build.id
                background_tasks.add_task(_run_onboarding, build_int_id)

    return {"ok": True}


def _run_onboarding(build_id: int) -> None:
    from outreach_engine import trigger_onboarding
    trigger_onboarding(build_id)


# Debug helper: confirm builds are saving (admin only)
@app.get("/debug/builds")
async def debug_builds(request: Request, db: Session = Depends(get_db)):
    user_id = require_session(request)
    if not user_id:
        return RedirectResponse(url="/login", status_code=303)
    rows = db.query(Build).order_by(Build.id.desc()).limit(10).all()
    return [
        {
            "id": b.id,
            "build_id": b.build_id,
            "email": b.email,
            "package_tier": b.package_tier,
            "lead_volume_tier": b.lead_volume_tier,
            "status": b.status,
            "deposit_paid": b.deposit_paid,
            "deposit_payment_intent_id": b.deposit_payment_intent_id,
        }
        for b in rows
    ]


# ---------------------------------------------------------------------
# LEAD CAPTURE
# ---------------------------------------------------------------------


@app.post("/lead")
async def submit_lead(request: Request, background_tasks: BackgroundTasks):
    form = await request.form()

    hp = (form.get("hp_website") or form.get("website_hp") or "").strip()
    if hp:
        return RedirectResponse(url="/?thanks=1#lead-form", status_code=303)

    website = (form.get("website") or "").strip()

    name = (form.get("name") or "").strip()
    business = (form.get("business") or "").strip()
    email = (form.get("email") or "").strip().lower()
    budget = (form.get("budget") or "").strip()
    link = (form.get("link") or "").strip()
    role = (form.get("role") or "").strip()
    cadence = (form.get("cadence") or "").strip()
    recent = (form.get("recent") or "").strip()

    created_at = datetime.now(timezone.utc).isoformat()

    score, tier, score_notes = _score_lead(
        budget=budget,
        cadence=cadence,
        recent=recent,
        role=role,
        website=website,
        link=link,
        email=email,
    )

    try:
        db = SessionLocal()
        new_lead = Lead(
            created_at=created_at,
            name=name,
            email=email,
            business=business,
            website=website,
            budget=budget,
            link=link,
            role=role,
            cadence=cadence,
            recent=recent,
            status="new",
            run_count=0,
            last_run=None,
            score=score,
            tier=tier,
            score_notes=score_notes,
        )
        db.add(new_lead)
        db.commit()
    except Exception as exc:
        print("[lead] Error inserting lead:", exc)
        return RedirectResponse(url="/?error=1#lead-form", status_code=303)
    finally:
        try:
            db.close()
        except Exception:
            pass

    if email:
        admin_subj = "New Duding lead"
        admin_body = (
            f"New lead submitted:\n\n"
            f"Name: {name}\n"
            f"Email: {email}\n"
            f"Business: {business}\n"
            f"Website: {website}\n"
            f"Budget: {budget}\n"
            f"Link: {link}\n"
            f"Role: {role}\n"
            f"Cadence: {cadence}\n"
            f"Recent: {recent}\n"
            f"Score: {score}\n"
            f"Tier: {tier}\n"
            f"Created at: {created_at}\n"
        )
        background_tasks.add_task(
            send_email, ADMIN_NOTIFY_EMAIL, admin_subj, admin_body
        )

        lead_subj = "We received your info — Duding"
        lead_body = (
            "Hey,\n\n"
            "Thanks for submitting your info. "
            "We’ll review your details and follow up if you’re a fit.\n\n"
            "– Duding"
        )
        background_tasks.add_task(send_email, email, lead_subj, lead_body)

    return RedirectResponse(url="/?thanks=1#lead-form", status_code=303)


# ---------------------------------------------------------------------
# LOGIN / LOGOUT
# ---------------------------------------------------------------------


@app.get("/login", response_class=HTMLResponse)
async def login_get(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/login", response_class=HTMLResponse)
async def login_post(
    request: Request, email: str = Form(...), password: str = Form(...)
):
    e = (email or "").strip().lower()
    p = (password or "").strip()

    if e == ADMIN_EMAIL and p == ADMIN_PASSWORD:
        request.session["user_id"] = 1
        request.session["username"] = ADMIN_EMAIL
        return RedirectResponse(url="/dashboard", status_code=303)

    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": "Invalid credentials."},
        status_code=401,
    )


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


# ---------------------------------------------------------------------
# DASHBOARD / AUTOMATION
# ---------------------------------------------------------------------


@app.get("/dashboard/outreach", response_class=HTMLResponse)
async def outreach_dashboard(
    request: Request, job: Optional[str] = None, db: Session = Depends(get_db)
):
    user_id = require_session(request)
    if not user_id:
        return RedirectResponse(url="/login", status_code=303)

    _process_due_followups(db)

    prospects = (
        db.query(OutreachProspect).order_by(OutreachProspect.created_at.desc()).all()
    )
    activities = []
    for prospect in prospects:
        activities.extend(prospect.activities)

    return templates.TemplateResponse(
        "outreach.html",
        {
            "request": request,
            "user_id": user_id,
            "prospects": prospects,
            "activities": sorted(
                activities, key=lambda a: a.created_at or datetime.min, reverse=True
            ),
            "active_job": job or "",
        },
    )


@app.post("/dashboard/outreach/find")
async def find_and_outreach(
    request: Request,
    background_tasks: BackgroundTasks,
    search_terms: str = Form(""),
    max_per_term: int = Form(8),
):
    user_id = require_session(request)
    if not user_id:
        return RedirectResponse(url="/login", status_code=303)

    terms = [t.strip() for t in (search_terms or "").splitlines() if t.strip()]
    if not terms:
        return RedirectResponse(url="/dashboard/outreach", status_code=303)

    job_id = str(uuid.uuid4())[:8]
    _find_jobs[job_id] = {
        "status": "running",
        "terms": terms,
        "total": 0,
        "processed": 0,
        "sent": 0,
        "skipped": 0,
        "log": [f"Job started — searching {len(terms)} term(s)…"],
    }
    background_tasks.add_task(_run_find_and_outreach, job_id, terms, max_per_term)

    return RedirectResponse(url=f"/dashboard/outreach?job={job_id}", status_code=303)


@app.get("/dashboard/outreach/find/status/{job_id}")
async def find_job_status(request: Request, job_id: str):
    user_id = require_session(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    job = _find_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JSONResponse(job)


@app.get("/dashboard/outreach/preview")
async def outreach_preview(request: Request, target: str = "", db: Session = Depends(get_db)):
    """Dry-run: scrape a URL/search term and return the generated email without sending."""
    user_id = require_session(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    target = target.strip()
    if not target:
        return JSONResponse({"error": "No target provided"}, status_code=400)

    profile = scrape_business_profile(target)
    subject, body = build_outreach_email(profile, target)
    resolved_url = profile.get("final_url") or resolve_business_url(target) or None
    to_email, email_quality, email_note = _get_outreach_email_for_target(target, resolved_url or "")

    return JSONResponse({
        "to": to_email,
        "email_quality": email_quality,
        "email_note": email_note,
        "subject": subject,
        "body": body,
        "business_name": profile.get("business_name") or profile.get("website_title") or target,
        "industry": profile.get("industry") or "Service business",
        "services": profile.get("services_found") or [],
        "scraped": bool(profile.get("scraped")),
        "scrape_error": profile.get("error") or "",
    })


@app.get("/dashboard/outreach/{prospect_id}/email")
async def outreach_prospect_email(request: Request, prospect_id: int, db: Session = Depends(get_db)):
    """Return the stored email for a specific prospect."""
    user_id = require_session(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    prospect = db.query(OutreachProspect).filter(OutreachProspect.id == prospect_id).first()
    if not prospect:
        raise HTTPException(status_code=404, detail="Prospect not found")

    if not prospect.last_message:
        return JSONResponse({"error": "No email stored for this prospect yet."}, status_code=404)

    sent_at = (
        prospect.last_contacted_at.strftime("%b %d, %Y at %H:%M UTC")
        if prospect.last_contacted_at
        else None
    )

    return JSONResponse({
        "to": prospect.email,
        "subject": prospect.last_email_subject or "",
        "body": prospect.last_message,
        "business_name": prospect.business_name or prospect.source_input or "",
        "sent_at": sent_at,
        "status": prospect.status,
        "follow_up_count": prospect.follow_up_count,
    })


@app.post("/dashboard/outreach/run")
async def run_outreach(
    request: Request, targets: str = Form(""), db: Session = Depends(get_db)
):
    user_id = require_session(request)
    if not user_id:
        return RedirectResponse(url="/login", status_code=303)

    lines = [line.strip() for line in (targets or "").splitlines() if line.strip()]
    created_count = 0

    for target in lines:
        profile = scrape_business_profile(target)
        resolved_url = resolve_business_url(target)
        to_email, email_quality, email_note = _get_outreach_email_for_target(target, resolved_url)
        subject, body = build_outreach_email(profile, target)
        is_generic = email_quality == "generic"

        prospect = OutreachProspect(
            source_input=target,
            source_url=resolved_url or None,
            business_name=(
                profile.get("business_name") or profile.get("website_title") or target
            ).strip()
            or None,
            contact_name=None,
            email=to_email,
            email_quality=email_quality,
            email_note=email_note,
            website=profile.get("final_url") or resolved_url or None,
            business_description=(
                profile.get("website_description")
                or profile.get("industry")
                or "service business"
            )[:1000],
            lever=subject,
            status="pending_review" if is_generic else "outreach_pending",
            next_follow_up_at=datetime.now(timezone.utc) + timedelta(days=3),
            last_contacted_at=datetime.now(timezone.utc) if not is_generic else None,
            last_email_subject=subject,
            last_message=body,
        )
        db.add(prospect)
        db.flush()

        if not is_generic:
            send_email(prospect.email, subject, body)

        db.add(
            OutreachActivity(
                prospect_id=prospect.id,
                activity_type="email_sent",
                subject=subject,
                body_preview=body[:180],
                status="sent",
            )
        )
        db.add(
            OutreachActivity(
                prospect_id=prospect.id,
                activity_type="follow_up_scheduled",
                subject="Day 3 follow-up scheduled",
                body_preview="A follow-up is scheduled for day 3.",
                status="scheduled",
            )
        )
        db.add(
            OutreachActivity(
                prospect_id=prospect.id,
                activity_type="follow_up_scheduled",
                subject="Day 7 follow-up scheduled",
                body_preview="A follow-up is scheduled for day 7.",
                status="scheduled",
            )
        )
        created_count += 1

    db.commit()
    return RedirectResponse(url="/dashboard/outreach", status_code=303)


@app.post("/dashboard/outreach/{prospect_id}/status")
async def update_outreach_status(
    request: Request,
    prospect_id: int,
    status: str = Form("outreach_pending"),
    db: Session = Depends(get_db),
):
    user_id = require_session(request)
    if not user_id:
        return RedirectResponse(url="/login", status_code=303)

    prospect = (
        db.query(OutreachProspect).filter(OutreachProspect.id == prospect_id).first()
    )
    if prospect:
        prospect.status = status
        db.add(prospect)
        db.add(
            OutreachActivity(
                prospect_id=prospect.id,
                activity_type="status_updated",
                subject=f"Status -> {status}",
                body_preview=f"Prospect marked as {status}.",
                status=status,
            )
        )
        db.commit()

    return RedirectResponse(url="/dashboard/outreach", status_code=303)


@app.post("/dashboard/outreach/{prospect_id}/send")
async def send_pending_prospect(
    request: Request,
    prospect_id: int,
    db: Session = Depends(get_db),
):
    """Send email for a 'pending_review' prospect that was held due to a generic email address."""
    user_id = require_session(request)
    if not user_id:
        return RedirectResponse(url="/login", status_code=303)

    prospect = db.query(OutreachProspect).filter(OutreachProspect.id == prospect_id).first()
    if prospect and prospect.last_message:
        send_email(prospect.email, prospect.last_email_subject or "", prospect.last_message)
        prospect.status = "outreach_pending"
        prospect.last_contacted_at = datetime.now(timezone.utc)
        prospect.next_follow_up_at = datetime.now(timezone.utc) + timedelta(days=3)
        db.add(OutreachActivity(
            prospect_id=prospect.id,
            activity_type="email_sent",
            subject=prospect.last_email_subject or "",
            body_preview=(prospect.last_message or "")[:180],
            status="sent",
        ))
        db.commit()

    return RedirectResponse(url="/dashboard/outreach", status_code=303)


@app.get("/dashboard/outreach/engine/status")
async def engine_status(request: Request):
    require_session(request)
    from outreach_engine import get_status
    return JSONResponse(get_status())


@app.post("/dashboard/outreach/engine/pause")
async def engine_pause(request: Request):
    require_session(request)
    from outreach_engine import get_status, set_paused
    current = get_status()
    set_paused(not current["paused"])
    return JSONResponse(get_status())


# ── SECTION 10 — Business overview API ───────────────────────────────────────

@app.get("/api/business/overview")
async def business_overview(request: Request, db: Session = Depends(get_db)):
    require_session(request)
    now     = datetime.now(timezone.utc)
    today   = now.replace(hour=0, minute=0, second=0, microsecond=0)
    w_start = now - timedelta(days=7)
    m_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    sent_today = (
        db.query(OutreachActivity)
        .filter(
            OutreachActivity.activity_type.in_(["email_sent", "follow_up_sent"]),
            OutreachActivity.created_at >= today,
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
    hot_count = (
        db.query(OutreachProspect)
        .filter(OutreachProspect.status.in_(["replied", "booked"]))
        .count()
    )

    paid_builds = db.query(Build).filter(Build.deposit_paid == True).all()
    deposits_month = 0
    for b in paid_builds:
        paid_at = b.deposit_paid_at
        if paid_at:
            if paid_at.tzinfo is None:
                paid_at = paid_at.replace(tzinfo=timezone.utc)
            if paid_at >= m_start:
                deposits_month += 1
    revenue_total  = sum(b.deposit_amount_cents for b in paid_builds) / 100
    pipeline_value = sum(
        b.total_price_cents for b in paid_builds
        if b.status not in ("CANCELED", "LIVE")
    ) / 100

    # Next 3 follow-ups
    next_followups = (
        db.query(OutreachProspect)
        .filter(
            OutreachProspect.next_follow_up_at.isnot(None),
            OutreachProspect.status.in_(["outreach_pending", "follow_up_pending"]),
        )
        .order_by(OutreachProspect.next_follow_up_at.asc())
        .limit(3)
        .all()
    )

    # Latest reply
    latest_reply = (
        db.query(OutreachActivity)
        .filter(OutreachActivity.activity_type == "reply_received")
        .order_by(OutreachActivity.created_at.desc())
        .first()
    )

    # Pending drafts
    pending_drafts = (
        db.query(ClientDraft)
        .filter(ClientDraft.sent == False, ClientDraft.approved == False)
        .count()
    )

    # Builds needing attention (installing but no day3 yet after 3+ days)
    needs_attention = (
        db.query(Build)
        .filter(
            Build.status == "INSTALLING",
            Build.deposit_paid_at.isnot(None),
        )
        .count()
    )

    return JSONResponse({
        "sent_today":      sent_today,
        "replies_week":    replies_week,
        "total_prospects": total_prospects,
        "hot_count":       hot_count,
        "deposits_month":  deposits_month,
        "revenue_total":   revenue_total,
        "pipeline_value":  pipeline_value,
        "pending_drafts":  pending_drafts,
        "needs_attention": needs_attention,
        "next_followups": [
            {
                "name": p.business_name or p.email,
                "due":  p.next_follow_up_at.strftime("%b %d") if p.next_follow_up_at else "?",
                "status": p.status,
            }
            for p in next_followups
        ],
        "latest_reply": {
            "preview": latest_reply.body_preview or latest_reply.subject or "—",
            "when": latest_reply.created_at.strftime("%b %d %H:%M") if latest_reply.created_at else "—",
        } if latest_reply else None,
    })


# ── SECTION 2 — Draft approval routes ────────────────────────────────────────

@app.get("/dashboard/drafts", response_class=HTMLResponse)
async def drafts_page(request: Request, db: Session = Depends(get_db)):
    user_id = require_session(request)
    if not user_id:
        return RedirectResponse(url="/login", status_code=303)
    drafts = (
        db.query(ClientDraft)
        .filter(ClientDraft.sent == False)
        .order_by(ClientDraft.created_at.desc())
        .limit(50)
        .all()
    )
    return templates.TemplateResponse(
        "drafts.html", {"request": request, "user_id": user_id, "drafts": drafts}
    )


@app.post("/dashboard/drafts/{draft_id}/approve")
async def approve_draft(
    request: Request,
    draft_id: int,
    db: Session = Depends(get_db),
):
    user_id = require_session(request)
    if not user_id:
        return RedirectResponse(url="/login", status_code=303)
    draft = db.query(ClientDraft).filter(ClientDraft.id == draft_id).first()
    if draft and not draft.sent:
        from outreach_engine import _send
        ok = _send(draft.to_email, draft.subject, draft.body, from_name="Tommy")
        draft.sent     = ok
        draft.approved = True
        draft.sent_at  = datetime.now(timezone.utc) if ok else None
        db.add(draft)
        db.commit()
    return RedirectResponse(url="/dashboard/drafts", status_code=303)


@app.post("/dashboard/drafts/{draft_id}/discard")
async def discard_draft(
    request: Request,
    draft_id: int,
    db: Session = Depends(get_db),
):
    user_id = require_session(request)
    if not user_id:
        return RedirectResponse(url="/login", status_code=303)
    draft = db.query(ClientDraft).filter(ClientDraft.id == draft_id).first()
    if draft:
        draft.approved = True
        draft.sent     = False
        db.add(draft)
        db.commit()
    return RedirectResponse(url="/dashboard/drafts", status_code=303)


# ── SECTION 6 — Referral tracking ────────────────────────────────────────────

@app.get("/refer/{build_id}", response_class=HTMLResponse)
async def referral_landing(request: Request, build_id: int, db: Session = Depends(get_db)):
    """Log inbound referral click and redirect to homepage."""
    build = db.query(Build).filter(Build.id == build_id).first()
    if build:
        db.add(Referral(
            referrer_build_id=build_id,
            referrer_email=build.email,
            referrer_business=build.business_name,
        ))
        db.commit()
    return RedirectResponse(url="/", status_code=302)


# ── RETAINER ROUTES ──────────────────────────────────────────────────────────

APP_BASE_URL = os.getenv("APP_BASE_URL", "https://duding.ai")

RETAINER_TIERS = {
    "growth": {"label": "Growth Retainer", "price": "$997/month"},
    "scale":  {"label": "Scale Retainer",  "price": "$1,497/month"},
}


@app.get("/retainer/accept/{build_id}/{tier}", response_class=HTMLResponse)
async def retainer_accept(request: Request, build_id: str, tier: str, db: Session = Depends(get_db)):
    """Client clicks accept link from email — record intent, redirect to onboard form."""
    if tier not in RETAINER_TIERS:
        raise HTTPException(status_code=404, detail="Unknown tier")

    build = db.query(Build).filter(Build.build_id == build_id).first()
    if not build:
        raise HTTPException(status_code=404, detail="Build not found")

    existing = db.query(RetainerClient).filter(
        RetainerClient.build_id == build_id,
        RetainerClient.tier == tier,
    ).first()
    if not existing:
        rc = RetainerClient(
            build_id=build_id,
            tier=tier,
            status="pending_onboard",
            email=build.email,
            contact_name=build.contact_name,
            business_name=build.business_name,
            accepted_at=datetime.now(timezone.utc),
        )
        db.add(rc)
        db.commit()

    return RedirectResponse(url=f"/retainer/onboard/{build_id}/{tier}", status_code=302)


@app.get("/retainer/onboard/{build_id}/{tier}", response_class=HTMLResponse)
async def retainer_onboard_get(request: Request, build_id: str, tier: str, db: Session = Depends(get_db)):
    if tier not in RETAINER_TIERS:
        raise HTTPException(status_code=404, detail="Unknown tier")
    rc = db.query(RetainerClient).filter(
        RetainerClient.build_id == build_id,
        RetainerClient.tier == tier,
    ).first()
    if not rc:
        raise HTTPException(status_code=404, detail="No retainer record found — please use the link from your email.")
    tier_info = RETAINER_TIERS[tier]
    return templates.TemplateResponse("retainer_onboard.html", {
        "request": request,
        "rc": rc,
        "tier_info": tier_info,
        "submitted": False,
    })


@app.post("/retainer/onboard/{build_id}/{tier}", response_class=HTMLResponse)
async def retainer_onboard_post(
    request: Request,
    build_id: str,
    tier: str,
    ad_account_email: str = Form(""),
    social_logins: str = Form(""),
    brand_assets_note: str = Form(""),
    db: Session = Depends(get_db),
):
    if tier not in RETAINER_TIERS:
        raise HTTPException(status_code=404, detail="Unknown tier")
    rc = db.query(RetainerClient).filter(
        RetainerClient.build_id == build_id,
        RetainerClient.tier == tier,
    ).first()
    if not rc:
        raise HTTPException(status_code=404, detail="Retainer record not found")

    rc.ad_account_email = ad_account_email.strip() or None
    rc.social_logins = social_logins.strip() or None
    rc.brand_assets_note = brand_assets_note.strip() or None
    rc.status = "active"
    rc.onboarded_at = datetime.now(timezone.utc)
    db.add(rc)
    db.commit()

    tier_info = RETAINER_TIERS[tier]
    send_email(
        ADMIN_NOTIFY_EMAIL,
        f"Retainer accepted — {rc.business_name or rc.email} ({tier_info['label']})",
        f"New retainer client onboarded.\n\n"
        f"Business: {rc.business_name}\nContact: {rc.contact_name}\nEmail: {rc.email}\n"
        f"Tier: {tier_info['label']} {tier_info['price']}\n\n"
        f"Ad account email: {rc.ad_account_email or '—'}\n"
        f"Social logins: {rc.social_logins or '—'}\n"
        f"Brand assets: {rc.brand_assets_note or '—'}",
    )

    return templates.TemplateResponse("retainer_onboard.html", {
        "request": request,
        "rc": rc,
        "tier_info": tier_info,
        "submitted": True,
    })


@app.get("/dashboard/retainer-clients", response_class=HTMLResponse)
async def retainer_clients_dashboard(request: Request, db: Session = Depends(get_db)):
    user_id = require_session(request)
    if not user_id:
        return RedirectResponse(url="/login", status_code=303)
    clients = db.query(RetainerClient).order_by(RetainerClient.created_at.desc()).all()
    return templates.TemplateResponse("retainer_clients.html", {
        "request": request,
        "user_id": user_id,
        "clients": clients,
        "RETAINER_TIERS": RETAINER_TIERS,
    })


# ── SECTION 8 — Content ideas dashboard ──────────────────────────────────────

@app.get("/dashboard/content", response_class=HTMLResponse)
async def content_ideas_page(request: Request, db: Session = Depends(get_db)):
    user_id = require_session(request)
    if not user_id:
        return RedirectResponse(url="/login", status_code=303)
    ideas = (
        db.query(ContentIdea)
        .order_by(ContentIdea.created_at.desc())
        .limit(100)
        .all()
    )
    return templates.TemplateResponse(
        "content.html", {"request": request, "user_id": user_id, "ideas": ideas}
    )


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    user_id = require_session(request)
    if not user_id:
        return RedirectResponse(url="/login", status_code=303)

    rows = []
    try:
        db = SessionLocal()
        rows = [_lead_to_dict(l) for l in db.query(Lead).order_by(Lead.id.desc()).all()]
    except Exception as exc:
        print("[dashboard] DB error:", exc)
    finally:
        try:
            db.close()
        except Exception:
            pass

    total_leads = len(rows)
    now_utc = datetime.now(timezone.utc)
    seven_days_ago = now_utc - timedelta(days=7)
    last_7_days = 0
    unique_emails: Set[str] = set()

    hot = warm = cold = 0

    for r in rows:
        em = r["email"]
        if em:
            unique_emails.add(em.lower())

        created_dt = parse_created_to_utc(r["created"])
        if created_dt and created_dt >= seven_days_ago:
            last_7_days += 1

        t = (r["tier"] or "cold").lower()
        if t == "hot":
            hot += 1
        elif t == "warm":
            warm += 1
        else:
            cold += 1

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user_id": user_id,
            "admin_name": ADMIN_NAME,
            "leads": rows,
            "total": total_leads,
            "last7": last_7_days,
            "unique": len(unique_emails),
            "hot": hot,
            "warm": warm,
            "cold": cold,
        },
    )


@app.get("/automation", response_class=HTMLResponse)
async def automation(request: Request):
    user_id = require_session(request)
    if not user_id:
        return RedirectResponse(url="/login", status_code=303)

    rows = []
    try:
        db = SessionLocal()
        rows = [_lead_to_dict(l) for l in db.query(Lead).order_by(Lead.id.desc()).all()]
    except Exception as exc:
        print("[automation] DB error:", exc)
    finally:
        try:
            db.close()
        except Exception:
            pass

    return templates.TemplateResponse(
        "automation.html",
        {"request": request, "user_id": user_id, "leads": rows},
    )


@app.get("/automation/run")
async def automation_run(request: Request, lead_id: int):
    user_id = require_session(request)
    if not user_id:
        return RedirectResponse(url="/login", status_code=303)

    now = datetime.now(timezone.utc).isoformat()

    try:
        db = SessionLocal()
        lead = db.query(Lead).filter(Lead.id == lead_id).first()
        if lead:
            lead.status = "completed"
            lead.run_count = (lead.run_count or 0) + 1
            lead.last_run = now
            db.commit()
    except Exception as exc:
        print("[automation_run] DB error:", exc)
    finally:
        try:
            db.close()
        except Exception:
            pass

    ref = request.headers.get("referer") or "/automation"
    return RedirectResponse(url=ref, status_code=303)


# ---------------------------------------------------------------------
# BUSINESS PROFILE + QUOTE ROUTES
# ---------------------------------------------------------------------


def _get_lead_row(lead_id: int) -> Optional[dict]:
    try:
        db = SessionLocal()
        lead = db.query(Lead).filter(Lead.id == lead_id).first()
        return _lead_to_dict(lead) if lead else None
    except Exception as exc:
        print("[get_lead_row] DB error:", exc)
        return None
    finally:
        try:
            db.close()
        except Exception:
            pass


def _enrich_profile_with_scan(profile: dict, row: Optional[dict]) -> dict:
    try:
        website = (
            profile.get("website") or (row["website"] if row else "") or ""
        ).strip()
        if not website:
            return profile

        scan = scan_site(website) or {}

        business = profile.get("business")
        if not isinstance(business, dict):
            business = {}
            profile["business"] = business

        business["website_title"] = scan.get("website_title")
        business["website_description"] = scan.get("website_description")
        business["phones_found"] = scan.get("phones_found", []) or []
        business["social_links"] = scan.get("social_links", []) or []
        business["services_found"] = scan.get("services_found", []) or []
        profile["scraped"] = bool(scan.get("scraped", False))

        profile["website"] = website
        return profile
    except Exception as exc:
        print("[scan] Failed to enrich profile:", exc)
        return profile


@app.get("/debug/profile/{lead_id}")
async def debug_profile(request: Request, lead_id: int):
    user_id = require_session(request)
    if not user_id:
        return RedirectResponse(url="/login", status_code=303)

    row = _get_lead_row(lead_id)
    if not row:
        return JSONResponse({"error": "Lead not found"}, status_code=404)

    profile = build_profile_from_lead_row(row)
    profile = _enrich_profile_with_scan(profile, row)
    return JSONResponse(profile)


@app.get("/profile/{lead_id}", response_class=HTMLResponse)
async def profile_view(request: Request, lead_id: int):
    user_id = require_session(request)
    if not user_id:
        return RedirectResponse(url="/login", status_code=303)

    row = _get_lead_row(lead_id)
    if not row:
        return RedirectResponse(url="/dashboard", status_code=303)

    profile = build_profile_from_lead_row(row)
    profile = _enrich_profile_with_scan(profile, row)

    return templates.TemplateResponse(
        "profile.html", {"request": request, "profile": profile}
    )


@app.get("/quote/{lead_id}", response_class=HTMLResponse)
async def quote_view(request: Request, lead_id: int):
    row = _get_lead_row(lead_id)
    if not row:
        return RedirectResponse(url="/", status_code=303)

    profile = build_profile_from_lead_row(row)
    profile = _enrich_profile_with_scan(profile, row)

    base_url = str(request.base_url).rstrip("/")
    static_base = f"{base_url}/static"

    return templates.TemplateResponse(
        "quote.html",
        {
            "request": request,
            "profile": profile,
            "quote_mode": True,
            "static_base": static_base,
        },
    )


# ---------------------------------------------------------------------
# DASHBOARD LEADS LIST + DETAIL + QUOTE SEND (legacy)
# ---------------------------------------------------------------------


@app.get("/dashboard/leads", response_class=HTMLResponse)
async def leads_list(request: Request, q: str = ""):
    user_id = require_session(request)
    if not user_id:
        return RedirectResponse(url="/login", status_code=303)

    rows = []
    q = (q or "").strip()
    try:
        db = SessionLocal()
        query = db.query(Lead)
        if q:
            like = f"%{q}%"
            query = query.filter(
                Lead.name.ilike(like)
                | Lead.email.ilike(like)
                | Lead.business.ilike(like)
                | Lead.link.ilike(like)
            )
        rows = [_lead_to_dict(l) for l in query.order_by(Lead.id.desc()).all()]
    except Exception as exc:
        print("[leads_list] DB error:", exc)
    finally:
        try:
            db.close()
        except Exception:
            pass

    total = len(rows)
    return templates.TemplateResponse(
        "leads.html",
        {
            "request": request,
            "leads": rows,
            "total": total,
            "page": 1,
            "pages": 1,
            "size": total,
            "q": q,
        },
    )


@app.get("/dashboard/leads/{lead_id}", response_class=HTMLResponse)
async def lead_detail(request: Request, lead_id: int):
    user_id = require_session(request)
    if not user_id:
        return RedirectResponse(url="/login", status_code=303)

    lead_row = _get_lead_row(lead_id)
    if not lead_row:
        return RedirectResponse(url="/dashboard/leads", status_code=303)

    events: list[LeadEvent] = []
    try:
        db = SessionLocal()
        events = (
            db.query(LeadEvent)
            .filter(LeadEvent.lead_id == lead_id)
            .order_by(LeadEvent.created_at.asc())
            .all()
        )
    except Exception as exc:
        print("[lead_detail] error loading events:", exc)
    finally:
        try:
            db.close()
        except Exception:
            pass

    return templates.TemplateResponse(
        "lead_detail.html", {"request": request, "lead": lead_row, "events": events}
    )


@app.post("/dashboard/leads/{lead_id}/quote/send")
async def send_quote(request: Request, lead_id: int, background_tasks: BackgroundTasks):
    user_id = require_session(request)
    if not user_id:
        return RedirectResponse(url="/login", status_code=303)

    row = _get_lead_row(lead_id)
    if not row:
        return RedirectResponse(url="/dashboard/leads", status_code=303)

    lead_email = (row["email"] or "").strip()
    if not lead_email:
        return RedirectResponse(url=f"/dashboard/leads/{lead_id}", status_code=303)

    base_url = str(request.base_url).rstrip("/")
    quote_link = f"{base_url}/quote/{lead_id}"

    # Pull lead context for personalization
    raw_name = (row["name"] or "").strip()
    first_name = raw_name.split()[0] if raw_name else ""
    business = (row["business"] or "").strip()
    greeting = first_name if first_name else "Hey"
    biz_line = f" for {business}" if business else ""

    subject = (
        f"Your intake OS plan is ready, {first_name}"
        if first_name
        else f"Your Duding build plan is ready{biz_line}"
    )

    # ── Plain-text fallback ───────────────────────────────────────────────
    body_text = (
        f"{greeting},\n\n"
        f"I put together a build plan{biz_line} — based on a scan of your site "
        "and what you shared. Not a template.\n\n"
        "Inside the plan:\n"
        "  • What we found on your site (phone tracking, forms, gaps)\n"
        "  • Where jobs are most likely leaking right now\n"
        "  • Exactly what we’d install first and how long it takes\n\n"
        f"View your build plan here:\n{quote_link}\n\n"
        "If it looks like a fit, reply to this email and we’ll confirm the details. "
        "We take a limited number of installs — the deposit holds your slot.\n\n"
        "– Tommy at Duding\n\n"
        "--\n"
        "Duding.ai | Lead Intake OS for service businesses\n"
        f"{base_url}"
    )

    # ── HTML email ────────────────────────────────────────────────────────
    biz_display = f"<strong>{business}</strong>" if business else "your business"
    name_display = f"<strong>{first_name}</strong>" if first_name else ""

    body_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>{subject}</title>
</head>
<body style="margin:0;padding:0;background:#f4f4f5;font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;color:#111827;">
<table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="background:#f4f4f5;padding:32px 16px;color:#111827;">
  <tr><td align="center">
    <table width="600" cellpadding="0" cellspacing="0" role="presentation"
           style="max-width:600px;width:100%;background:#ffffff;border-radius:16px;overflow:hidden;box-shadow:0 8px 32px rgba(0,0,0,.08);">

      <!-- Brand bar -->
      <tr>
        <td style="background:#0b0b0c;padding:16px 24px;">
          <span style="color:#ffffff;font-size:16px;font-weight:800;letter-spacing:.06em;">duding</span>
          <span style="color:rgba(255,255,255,.5);font-size:11px;letter-spacing:.18em;text-transform:uppercase;margin-left:8px;">INTAKE OS</span>
        </td>
      </tr>

      <!-- Body -->
      <tr>
        <td style="padding:32px 28px 8px;color:#111827;">

          <p style="margin:0 0 20px;font-size:15px;line-height:1.6;color:#111827;">
            {"Hey " + name_display if name_display else "Hey"},
          </p>

          <p style="margin:0 0 16px;font-size:15px;line-height:1.6;color:#111827;">
            I put together a build plan for {biz_display} — based on a scan of your site
            and the details you shared. Not a template.
          </p>

          <!-- What’s inside box -->
          <table width="100%" cellpadding="0" cellspacing="0" role="presentation"
                 style="background:#f9fafb;border:1px solid #e5e7eb;border-radius:12px;margin:0 0 24px;">
            <tr>
              <td style="padding:16px 20px;">
                <p style="margin:0 0 10px;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.12em;color:#9ca3af;">
                  Inside the plan
                </p>
                <table cellpadding="0" cellspacing="0" role="presentation">
                  <tr>
                    <td style="padding:4px 0;font-size:14px;color:#374151;">
                      &#x2192;&nbsp; What we found on your site (phone tracking, forms, gaps)
                    </td>
                  </tr>
                  <tr>
                    <td style="padding:4px 0;font-size:14px;color:#374151;">
                      &#x2192;&nbsp; Where jobs are most likely leaking right now
                    </td>
                  </tr>
                  <tr>
                    <td style="padding:4px 0;font-size:14px;color:#374151;">
                      &#x2192;&nbsp; Exactly what we&#x27;d install first and the timeline
                    </td>
                  </tr>
                </table>
              </td>
            </tr>
          </table>

          <!-- CTA button -->
          <table cellpadding="0" cellspacing="0" role="presentation" style="margin:0 0 24px;">
            <tr>
              <td style="border-radius:999px;background:#ff4d5a;">
                <a href="{quote_link}"
                   style="display:inline-block;padding:14px 28px;font-size:15px;font-weight:700;color:#ffffff;text-decoration:none;border-radius:999px;letter-spacing:.01em;">
                  View your build plan &rarr;
                </a>
              </td>
            </tr>
          </table>

          <p style="margin:0 0 8px;font-size:14px;line-height:1.6;color:#6b7280;">
            If it looks like a fit, just reply to this email. We take a limited number of
            installs — a $500 deposit holds your build slot.
          </p>

          <p style="margin:24px 0 0;font-size:14px;color:#111827;">
            &ndash; Tommy at Duding
          </p>

        </td>
      </tr>

      <!-- Divider -->
      <tr>
        <td style="padding:24px 28px 0;">
          <div style="height:1px;background:#f3f4f6;"></div>
        </td>
      </tr>

      <!-- Footer -->
      <tr>
        <td style="padding:16px 28px 24px;">
          <p style="margin:0;font-size:12px;color:#9ca3af;line-height:1.5;">
            Duding.ai &mdash; Lead Intake OS for service businesses<br/>
            You’re receiving this because you requested an install plan.
            <a href="{base_url}" style="color:#9ca3af;">{base_url}</a>
          </p>
        </td>
      </tr>

    </table>
  </td></tr>
</table>
</body>
</html>"""

    background_tasks.add_task(
        send_html_email, lead_email, subject, body_text, body_html
    )

    try:
        db = SessionLocal()
        db.add(
            LeadEvent(
                lead_id=lead_id,
                event_type="quote_sent",
                message="Quote emailed as link.",
            )
        )
        db.commit()
    except Exception as exc:
        print("[quote_send] Failed to log LeadEvent:", exc)
    finally:
        try:
            db.close()
        except Exception:
            pass

    return RedirectResponse(url=f"/dashboard/leads/{lead_id}", status_code=303)


# ---------------------------------------------------------------------
# API ROUTES (SQLAlchemy) - left as-is
# ---------------------------------------------------------------------


def run_lead_intake_workflow(db: Session, lead: Lead) -> None:
    existing = (
        db.query(LeadEvent)
        .filter(LeadEvent.lead_id == lead.id, LeadEvent.event_type == "lead_created")
        .first()
    )

    source = getattr(lead, "source", None) or "unknown"

    if not existing:
        db.add(
            LeadEvent(
                lead_id=lead.id,
                event_type="lead_created",
                message=f"Lead created from {source}",
            )
        )

    if not getattr(lead, "status", None):
        lead.status = "new"

    lead.status = "intake_in_progress"

    db.add(
        LeadEvent(
            lead_id=lead.id,
            event_type="intake_started",
            message="Intake workflow started (stub)",
        )
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)


@app.post("/api/leads", response_model=LeadRead)
async def api_create_lead(
    payload: LeadCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    created_at = datetime.now(timezone.utc).isoformat()

    lead = Lead(
        created_at=created_at,
        name=payload.name,
        email=(payload.email or "").lower() if payload.email else None,
        business=payload.business,
        budget=payload.budget,
        link=payload.link,
        role=payload.role,
        cadence=payload.cadence,
        recent=payload.recent,
        status="new",
        run_count=0,
        last_run=None,
    )

    db.add(lead)
    db.commit()
    db.refresh(lead)

    run_lead_intake_workflow(db, lead)
    return lead


@app.get("/api/leads", response_model=list[LeadRead])
async def api_list_leads(
    skip: int = 0, limit: int = 100, db: Session = Depends(get_db)
):
    return db.query(Lead).order_by(Lead.id.desc()).offset(skip).limit(limit).all()


@app.get("/api/leads/{lead_id}", response_model=LeadRead)
async def api_get_lead(lead_id: int, db: Session = Depends(get_db)):
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


@app.get("/api/leads/{lead_id}/events", response_model=list[LeadEventRead])
async def api_get_lead_events(lead_id: int, db: Session = Depends(get_db)):
    return (
        db.query(LeadEvent)
        .filter(LeadEvent.lead_id == lead_id)
        .order_by(LeadEvent.created_at.asc())
        .all()
    )


# ---------------------------------------------------------------------
# EXPORT ROUTES (legacy)
# ---------------------------------------------------------------------


@app.get("/export/csv")
async def export_csv(request: Request):
    user_id = require_session(request)
    if not user_id:
        return RedirectResponse(url="/login", status_code=303)

    try:
        db = SessionLocal()
        rows = [_lead_to_dict(l) for l in db.query(Lead).order_by(Lead.id.desc()).all()]
    except Exception as exc:
        print("[export_csv] DB error:", exc)
        rows = []
    finally:
        try:
            db.close()
        except Exception:
            pass

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(
        [
            "id",
            "created_at",
            "name",
            "email",
            "business",
            "website",
            "budget",
            "link",
            "role",
            "cadence",
            "recent",
            "status",
            "run_count",
            "last_run",
            "score",
            "tier",
            "score_notes",
        ]
    )
    for r in rows:
        writer.writerow(
            [
                r["id"],
                r["created_at"],
                r["name"],
                r["email"],
                r["business"],
                r["website"],
                r["budget"],
                r["link"],
                r["role"],
                r["cadence"],
                r["recent"],
                r["status"],
                r["run_count"],
                r["last_run"],
                r["score"],
                r["tier"],
                r["score_notes"],
            ]
        )

    output.seek(0)
    headers = {"Content-Disposition": 'attachment; filename="leads.csv"'}
    return StreamingResponse(output, media_type="text/csv", headers=headers)


@app.get("/export/txt")
async def export_txt(request: Request):
    user_id = require_session(request)
    if not user_id:
        return RedirectResponse(url="/login", status_code=303)

    emails: Set[str] = set()
    try:
        db = SessionLocal()
        for lead in db.query(Lead).filter(Lead.email.isnot(None), Lead.email != "").all():
            em = (lead.email or "").strip().lower()
            if em:
                emails.add(em)
    except Exception as exc:
        print("[export_txt] DB error:", exc)
    finally:
        try:
            db.close()
        except Exception:
            pass

    body = "\n".join(sorted(emails)) + "\n"
    headers = {"Content-Disposition": 'attachment; filename="emails.txt"'}
    return StreamingResponse(StringIO(body), media_type="text/plain", headers=headers)


# ---------------------------------------------------------------------
# CHKD CLIENT — webhook + admin dashboard
# ---------------------------------------------------------------------

@app.post("/chkd/webhook/profile")
async def chkd_profile_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Supabase database webhook — fires on INSERT into profiles table.
    Configure in Supabase dashboard: Database → Webhooks → profiles → INSERT.
    Set Authorization header to: Bearer {CHKD_WEBHOOK_SECRET}
    """
    if CHKD_WEBHOOK_SECRET:
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {CHKD_WEBHOOK_SECRET}":
            raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    if payload.get("type") != "INSERT":
        return {"ok": True, "skipped": "not an INSERT"}

    record  = payload.get("record") or {}
    user_id = str(record.get("id", ""))
    email   = record.get("email", "")
    name    = record.get("full_name") or record.get("name") or email

    # Supabase stores email in auth.users, not public.profiles.
    # If the record doesn't include it, look it up via the auth admin API.
    if not email and user_id:
        import httpx as _httpx
        _sb_url = "https://vmpoexkcdcsbufqxwdwe.supabase.co"
        _sb_key = os.getenv("SUPABASE_SERVICE_KEY", "")
        print(f"[chkd] webhook: email missing from record, key_set={bool(_sb_key)}, user={user_id}")
        if _sb_key:
            try:
                r = _httpx.get(
                    f"{_sb_url}/auth/v1/admin/users/{user_id}",
                    headers={"apikey": _sb_key, "Authorization": f"Bearer {_sb_key}"},
                    timeout=10,
                )
                print(f"[chkd] webhook: auth lookup status={r.status_code}")
                if r.status_code == 200:
                    auth_user = r.json()
                    email = auth_user.get("email", "")
                    meta  = auth_user.get("user_metadata", {})
                    if not name or name == user_id:
                        name = meta.get("full_name") or meta.get("name") or email
                    print(f"[chkd] webhook: resolved email={email} name={name}")
                else:
                    print(f"[chkd] webhook: auth lookup failed {r.status_code}: {r.text[:200]}")
            except Exception as exc:
                print(f"[chkd] webhook: auth lookup error: {exc}")
        else:
            print("[chkd] webhook: SUPABASE_SERVICE_KEY not set — cannot look up email")

    if not email:
        return {"ok": True, "skipped": "no email in record or auth lookup"}

    from services.chkd import build_welcome_email, send_chkd_email
    subj, body = build_welcome_email(name)
    sent = send_chkd_email(db, user_id, email, "welcome", subj, body)
    return {"ok": True, "sent": sent}


@app.get("/dashboard/chkd", response_class=HTMLResponse)
async def chkd_dashboard_redirect(request: Request, db: Session = Depends(get_db)):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=302)
    chkd = db.query(Client).filter(Client.domain == "getchkd.app").first()
    if chkd:
        return RedirectResponse(f"/dashboard/clients/{chkd.id}", status_code=302)
    return RedirectResponse("/dashboard/clients", status_code=302)


@app.get("/dashboard/clients", response_class=HTMLResponse)
async def clients_list(request: Request, db: Session = Depends(get_db)):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=302)

    clients = db.query(Client).order_by(Client.created_at.asc()).all()

    enriched = []
    for c in clients:
        extra: dict = {}
        if c.type == "internal":
            extra["emails_sent"] = db.query(ChkdEmail).count()
            last = db.query(ChkdEmail.sent_at).order_by(ChkdEmail.sent_at.desc()).scalar()
            extra["last_activity"] = last
        elif c.type == "install" and c.external_id:
            build = db.query(Build).filter(Build.build_id == c.external_id).first()
            extra["build"] = build
            extra["last_activity"] = getattr(build, "updated_at", None) if build else None
        elif c.type == "retainer" and c.external_id:
            try:
                rc = db.query(RetainerClient).filter(
                    RetainerClient.id == int(c.external_id)
                ).first()
            except (ValueError, TypeError):
                rc = None
            extra["retainer"] = rc
            extra["last_activity"] = getattr(rc, "onboarded_at", None) if rc else None
        enriched.append({"client": c, "extra": extra})

    return templates.TemplateResponse("clients_list.html", {
        "request":    request,
        "admin_name": ADMIN_NAME,
        "clients":    enriched,
    })


@app.post("/dashboard/clients", response_class=HTMLResponse)
async def client_create(
    request: Request,
    name:          str = Form(...),
    type:          str = Form("install"),
    status:        str = Form("active"),
    domain:        str = Form(""),
    dashboard_url: str = Form(""),
    external_id:   str = Form(""),
    notes:         str = Form(""),
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        raise HTTPException(status_code=403, detail="Forbidden")

    c = Client(
        name=name,
        type=type,
        status=status,
        domain=domain or None,
        dashboard_url=dashboard_url or None,
        external_id=external_id or None,
        notes=notes or None,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return RedirectResponse(f"/dashboard/clients/{c.id}", status_code=303)


@app.get("/dashboard/clients/{client_id}", response_class=HTMLResponse)
async def client_detail(request: Request, client_id: int, db: Session = Depends(get_db)):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=302)

    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    ctx: dict = {"request": request, "admin_name": ADMIN_NAME, "client": client}

    if client.type == "internal":
        from services.chkd import get_chkd_stats
        import json as _json
        ctx["stats"] = get_chkd_stats()
        ctx["recent_emails"] = (
            db.query(ChkdEmail).order_by(ChkdEmail.sent_at.desc()).limit(50).all()
        )
        # Latest social intelligence report
        latest_report = (
            db.query(SocialIntelligenceReport)
            .filter(SocialIntelligenceReport.client_id == client.id)
            .order_by(SocialIntelligenceReport.week_of.desc())
            .first()
        )
        ctx["latest_report"] = latest_report
        ctx["latest_analysis"] = (
            _json.loads(latest_report.analysis) if latest_report and latest_report.analysis else None
        )
        ctx["report_history"] = (
            db.query(SocialIntelligenceReport)
            .filter(SocialIntelligenceReport.client_id == client.id)
            .order_by(SocialIntelligenceReport.week_of.desc())
            .limit(8)
            .all()
        )
        # Content pieces — this week + recent
        from datetime import date as _date
        _today    = datetime.now(timezone.utc).date()
        _week_of  = _today - timedelta(days=_today.weekday())
        ctx["content_pieces"] = (
            db.query(ContentPiece)
            .filter(ContentPiece.client_id == client.id,
                    ContentPiece.week_of   == _week_of)
            .order_by(ContentPiece.id.asc())
            .all()
        )
        ctx["content_week_of"] = _week_of
        ctx["content_history_weeks"] = (
            db.query(ContentPiece.week_of)
            .filter(ContentPiece.client_id == client.id)
            .group_by(ContentPiece.week_of)
            .order_by(ContentPiece.week_of.desc())
            .limit(8)
            .all()
        )
        return templates.TemplateResponse("client_detail_internal.html", ctx)

    if client.type == "install":
        build = None
        if client.external_id:
            build = db.query(Build).filter(Build.build_id == client.external_id).first()
        ctx["build"] = build
        return templates.TemplateResponse("client_detail_install.html", ctx)

    if client.type == "retainer":
        rc = None
        if client.external_id:
            try:
                rc = db.query(RetainerClient).filter(
                    RetainerClient.id == int(client.external_id)
                ).first()
            except (ValueError, TypeError):
                pass
        ctx["retainer"] = rc
        return templates.TemplateResponse("client_detail_retainer.html", ctx)

    raise HTTPException(status_code=400, detail=f"Unknown client type: {client.type}")


# ---------------------------------------------------------------------
# CONTENT GENERATION — manual run, approve, download
# ---------------------------------------------------------------------

@app.post("/dashboard/clients/{client_id}/content/run")
async def content_run(
    request: Request,
    client_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        raise HTTPException(status_code=403)

    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404)

    def _run():
        from services.content_gen import run_weekly_content_gen
        run_weekly_content_gen(client_id, intel_report=None)

    background_tasks.add_task(_run)
    return RedirectResponse(
        f"/dashboard/clients/{client_id}?tab=content&running=1",
        status_code=303,
    )


@app.get("/dashboard/clients/{client_id}/content/week/{week_of}", response_class=HTMLResponse)
async def content_week_view(
    request: Request,
    client_id: int,
    week_of: str,
    db: Session = Depends(get_db),
):
    """View content pieces for any historical week."""
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=302)

    import json as _json
    from datetime import date as _date

    try:
        wdate = _date.fromisoformat(week_of)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid week_of date")

    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404)

    pieces = (
        db.query(ContentPiece)
        .filter(ContentPiece.client_id == client_id,
                ContentPiece.week_of   == wdate)
        .order_by(ContentPiece.id.asc())
        .all()
    )
    history_weeks = (
        db.query(ContentPiece.week_of)
        .filter(ContentPiece.client_id == client_id)
        .group_by(ContentPiece.week_of)
        .order_by(ContentPiece.week_of.desc())
        .limit(8)
        .all()
    )

    return templates.TemplateResponse("content_week.html", {
        "request":       request,
        "admin_name":    ADMIN_NAME,
        "client":        client,
        "pieces":        pieces,
        "week_of":       wdate,
        "history_weeks": history_weeks,
    })


@app.post("/dashboard/clients/{client_id}/content/{piece_id}/approve")
async def content_approve(
    request: Request,
    client_id: int,
    piece_id: int,
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        raise HTTPException(status_code=403)

    piece = db.query(ContentPiece).filter(
        ContentPiece.id == piece_id,
        ContentPiece.client_id == client_id,
    ).first()
    if not piece:
        raise HTTPException(status_code=404)

    piece.status = "approved" if piece.status != "approved" else "draft"
    db.commit()
    return RedirectResponse(
        f"/dashboard/clients/{client_id}?tab=content",
        status_code=303,
    )


@app.get("/dashboard/clients/{client_id}/content/{piece_id}/download")
async def content_download(
    request: Request,
    client_id: int,
    piece_id: int,
    slide: int = 0,
    db: Session = Depends(get_db),
):
    """Download a single PNG slide (slide=0 means first/only slide)."""
    import json as _json
    from fastapi.responses import Response

    if not request.session.get("user_id"):
        raise HTTPException(status_code=403)

    piece = db.query(ContentPiece).filter(
        ContentPiece.id == piece_id,
        ContentPiece.client_id == client_id,
    ).first()
    if not piece or not piece.image_data:
        raise HTTPException(status_code=404, detail="No image data")

    images = _json.loads(piece.image_data)
    if not images or slide >= len(images):
        raise HTTPException(status_code=404, detail="Slide not found")

    import base64 as _b64
    png_bytes = _b64.b64decode(images[slide])
    safe_title = (piece.title or "chkd").replace(" ", "_").lower()[:30]
    filename   = f"chkd_{safe_title}_slide{slide + 1}.png"
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------
# SOCIAL INTELLIGENCE — manual run + report view
# ---------------------------------------------------------------------

@app.post("/dashboard/clients/{client_id}/social-intelligence/run")
async def social_intel_run(
    request: Request,
    client_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        raise HTTPException(status_code=403)

    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404)

    def _run():
        from services.social_intelligence import run_weekly_intelligence
        run_weekly_intelligence(client_id)

    background_tasks.add_task(_run)
    return RedirectResponse(
        f"/dashboard/clients/{client_id}?tab=social-intel&running=1",
        status_code=303,
    )


@app.get("/dashboard/clients/{client_id}/social-intelligence/{report_id}", response_class=HTMLResponse)
async def social_intel_report(
    request: Request,
    client_id: int,
    report_id: int,
    db: Session = Depends(get_db),
):
    if not request.session.get("user_id"):
        return RedirectResponse("/login", status_code=302)

    import json as _json
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404)

    report = db.query(SocialIntelligenceReport).filter(
        SocialIntelligenceReport.id        == report_id,
        SocialIntelligenceReport.client_id == client_id,
    ).first()
    if not report:
        raise HTTPException(status_code=404)

    analysis = _json.loads(report.analysis) if report.analysis else {}

    return templates.TemplateResponse("social_intel_report.html", {
        "request":  request,
        "admin_name": ADMIN_NAME,
        "client":   client,
        "report":   report,
        "analysis": analysis,
    })


# ---------------------------------------------------------------------
# HEALTH
# ---------------------------------------------------------------------


@app.get("/health")
async def health():
    return {"status": "ok"}
