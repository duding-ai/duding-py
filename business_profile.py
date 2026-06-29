import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup


# -----------------------------
# Hard rules
# -----------------------------
TIMEOUT_SEC = 10
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DudingScraper/1.0"


# -----------------------------
# Helpers
# -----------------------------
def _clean(s: Optional[str]) -> str:
    if not s:
        return ""
    s = re.sub(r"\s+", " ", str(s)).strip()
    return s


def _norm_url(u: str) -> str:
    u = _clean(u)
    if not u:
        return ""
    if not u.startswith(("http://", "https://")):
        u = "https://" + u
    return u


def _domain(u: str) -> str:
    try:
        p = urlparse(u)
        d = p.netloc.lower()
        d = d.replace("www.", "")
        return d
    except Exception:
        return ""


def _uniq(seq: List[str]) -> List[str]:
    seen = set()
    out = []
    for x in seq:
        x = _clean(x)
        if not x:
            continue
        k = x.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(x)
    return out


def _looks_like_service_line(s: str) -> bool:
    s = _clean(s)
    if len(s) < 3:
        return False
    # avoid junk
    junk = ["privacy", "terms", "copyright", "cookie", "login", "sign in", "careers"]
    if any(j in s.lower() for j in junk):
        return False
    # too long usually means paragraph not service
    if len(s) > 90:
        return False
    return True


def _extract_phones(text: str) -> List[str]:
    # US-ish phone patterns
    phones = re.findall(
        r"(?:(?:\+?1[\s\-\.])?)?\(?\d{3}\)?[\s\-\.]?\d{3}[\s\-\.]?\d{4}", text
    )
    cleaned = []
    for p in phones:
        p2 = re.sub(r"[^\d]", "", p)
        # keep 10 digits (or 11 starting with 1)
        if len(p2) == 11 and p2.startswith("1"):
            p2 = p2[1:]
        if len(p2) == 10:
            cleaned.append(f"({p2[0:3]}) {p2[3:6]}-{p2[6:10]}")
    return _uniq(cleaned)


def _extract_social_links(soup: BeautifulSoup, base_url: str) -> List[str]:
    socials = []
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if not href:
            continue
        href = href.strip()
        if href.startswith("/"):
            href = urljoin(base_url, href)
        low = href.lower()
        if any(
            x in low
            for x in [
                "facebook.com",
                "instagram.com",
                "tiktok.com",
                "youtube.com",
                "linkedin.com",
                "x.com",
                "twitter.com",
            ]
        ):
            socials.append(href)
    return _uniq(socials)


def _get_title_and_desc(soup: BeautifulSoup) -> Tuple[str, str]:
    title = ""
    desc = ""

    # title
    if soup.title and soup.title.text:
        title = _clean(soup.title.text)

    # meta description
    m = soup.find("meta", attrs={"name": "description"})
    if m and m.get("content"):
        desc = _clean(m.get("content"))

    # og:description fallback
    if not desc:
        og = soup.find("meta", attrs={"property": "og:description"})
        if og and og.get("content"):
            desc = _clean(og.get("content"))

    return title, desc


def _pull_visible_text(soup: BeautifulSoup) -> str:
    # Remove scripts/styles
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator=" ")
    return _clean(text)


# -----------------------------
# Service extraction (the core)
# -----------------------------
SERVICE_KEYWORDS = [
    # service business
    "roof",
    "roofing",
    "siding",
    "gutters",
    "hvac",
    "air conditioning",
    "heating",
    "furnace",
    "ac repair",
    "plumbing",
    "drain",
    "sewer",
    "water heater",
    "electric",
    "electrical",
    "panel",
    "wiring",
    "pest",
    "termite",
    "extermin",
    "landscap",
    "lawn",
    "tree",
    "mulch",
    "irrigation",
    "pressure washing",
    "power washing",
    "cleaning",
    "maid",
    "janitorial",
    "carpet",
    "painting",
    "floor",
    "tile",
    "garage door",
    "moving",
    "junk removal",
    "locksmith",
    "insurance",
    "health insurance",
    "auto insurance",
    "home insurance",
    "medicare",
    "dentist",
    "dental",
    "orthodont",
    "chiropract",
    "physical therapy",
    "law",
    "attorney",
    "legal",
    "real estate",
    "solar",
]


def _extract_services(soup: BeautifulSoup, base_url: str) -> List[str]:
    candidates: List[str] = []

    # 1) Nav / menus
    for a in soup.select("nav a, header a, .menu a, .navbar a, .nav a"):
        t = _clean(a.get_text(" "))
        if _looks_like_service_line(t):
            candidates.append(t)

    # 2) Headings (often list services)
    for h in soup.select("h1, h2, h3"):
        t = _clean(h.get_text(" "))
        if _looks_like_service_line(t):
            candidates.append(t)

    # 3) Bullet lists near "Services"
    for ul in soup.select("ul"):
        li_texts = [_clean(li.get_text(" ")) for li in ul.select("li")]
        # small bullet lists are usually services
        if 2 <= len(li_texts) <= 12:
            for t in li_texts:
                if _looks_like_service_line(t):
                    candidates.append(t)

    candidates = _uniq(candidates)

    # 4) Keyword filter: keep only lines likely to be services
    filtered: List[str] = []
    for c in candidates:
        low = c.lower()
        # keep if it contains a service keyword OR looks like a service phrase
        if any(k in low for k in SERVICE_KEYWORDS):
            filtered.append(c)
        else:
            # heuristic: "X repair", "X installation", "X services"
            if re.search(
                r"\b(repair|installation|install|services|service|replacement|inspection)\b",
                low,
            ):
                filtered.append(c)

    filtered = _uniq(filtered)

    # 5) Clean obvious non-services
    bad = {
        "home",
        "about",
        "contact",
        "blog",
        "careers",
        "login",
        "sign in",
        "privacy policy",
        "terms",
    }
    filtered2 = []
    for f in filtered:
        if f.strip().lower() in bad:
            continue
        filtered2.append(f)

    # limit (keep it readable)
    return filtered2[:10]


def _infer_industry(services: List[str], title: str, desc: str) -> str:
    blob = " ".join([title, desc] + services).lower()

    mapping = [
        ("HVAC", ["hvac", "air conditioning", "heating", "furnace", "ac repair"]),
        ("Plumbing", ["plumbing", "water heater", "sewer", "drain"]),
        ("Roofing", ["roof", "roofing", "gutters", "siding"]),
        ("Electrical", ["electrical", "electric", "panel", "wiring"]),
        ("Cleaning", ["cleaning", "maid", "janitorial", "carpet"]),
        ("Landscaping", ["landscap", "lawn", "tree", "mulch", "irrigation"]),
        ("Pest Control", ["pest", "termite", "extermin"]),
        (
            "Insurance",
            [
                "insurance",
                "medicare",
                "health insurance",
                "auto insurance",
                "home insurance",
            ],
        ),
        ("Legal", ["attorney", "law", "legal"]),
        ("Dental", ["dentist", "dental", "orthodont"]),
        ("Chiropractic / PT", ["chiropract", "physical therapy"]),
        ("Real Estate", ["real estate"]),
        ("Solar", ["solar"]),
    ]

    for industry, keys in mapping:
        if any(k in blob for k in keys):
            return industry

    return "Service business"


def _generate_bottlenecks(industry: str, signals: Dict[str, Any]) -> List[str]:
    # Use real signals to make these non-generic
    phones = signals.get("phones_found") or []
    socials = signals.get("social_links") or []

    out = []

    if not phones:
        out.append("No clear phone number detected on the site (lost-call risk).")
    else:
        out.append(
            "Multiple phone numbers found — could split tracking unless one is the ‘main’ number."
        )

    if not socials:
        out.append(
            "No social links detected — content distribution may be weak or disconnected."
        )
    else:
        out.append(
            "Social links detected — but likely not tied to lead tracking or retargeting yet."
        )

    if industry in [
        "HVAC",
        "Plumbing",
        "Roofing",
        "Electrical",
        "Cleaning",
        "Landscaping",
        "Pest Control",
    ]:
        out.append(
            "Most service sites leak leads from slow follow-up + missed calls (needs instant text-back)."
        )
        out.append(
            "Offers are usually unclear (need 1–2 ‘hero offers’ to run clean ads)."
        )

    if industry == "Insurance":
        out.append(
            "Insurance leads get expensive fast without tight qualification + fast follow-up."
        )
        out.append(
            "You need tracking by offer + channel or you’ll burn budget with no clarity."
        )

    return _uniq(out)[:6]


def _generate_levers(
    industry: str, services: List[str], signals: Dict[str, Any]
) -> Tuple[List[str], List[str], List[str], List[str]]:
    phones = signals.get("phones_found") or []
    has_forms = signals.get("forms_found") or False

    money = []
    time = []
    quick = []
    risks = []

    # Money levers (revenue)
    if services:
        money.append(
            f"Turn services into 1–2 clear offers (ex: “{services[0]}” → one simple CTA)."
        )
    else:
        money.append("Define 1–2 clear offers (service + price anchor + guarantee).")

    money.append(
        "Track every lead source (form + calls) so ads/data tell you what actually converts."
    )

    # Time levers (systems)
    time.append("Instant text-back + follow-up sequence within 60 seconds.")
    time.append("Missed-call text-back so you don’t lose callers.")
    time.append("Lead routing rules (good leads first, junk filtered).")

    # Quick wins
    if phones:
        quick.append(
            f"Make one ‘main number’ consistent everywhere (found: {phones[0]})."
        )
    else:
        quick.append("Add one ‘main number’ everywhere and track calls.")

    if not has_forms:
        quick.append(
            "Add a short ‘get quote’ form (name/email/need) to reduce drop-offs."
        )
    else:
        quick.append("Shorten the form (less friction) and track completion rate.")

    # Risks
    if not services:
        risks.append(
            "Services aren’t clearly stated (hard to build high-converting ads)."
        )
    if not phones:
        risks.append("No phone detected (calls may be leaking).")

    return (_uniq(money)[:5], _uniq(time)[:5], _uniq(quick)[:5], _uniq(risks)[:5])


# -----------------------------
# Website scrape
# -----------------------------
def _scrape_website(url: str) -> Dict[str, Any]:
    url = _norm_url(url)
    if not url:
        return {
            "scraped": False,
            "error": "No website provided",
        }

    try:
        r = requests.get(
            url, headers={"User-Agent": UA}, timeout=TIMEOUT_SEC, allow_redirects=True
        )
        html = r.text or ""
        soup = BeautifulSoup(html, "html.parser")

        title, desc = _get_title_and_desc(soup)
        visible_text = _pull_visible_text(soup)

        phones = _extract_phones(visible_text)
        socials = _extract_social_links(soup, r.url)

        # basic form detection
        forms_found = bool(soup.select("form input, form button"))

        services = _extract_services(soup, r.url)
        industry = _infer_industry(services, title, desc)

        return {
            "scraped": True,
            "final_url": r.url,
            "website_title": title,
            "website_description": desc,
            "visible_text_sample": visible_text[:800],  # for debugging only
            "services_found": services,
            "industry": industry,
            "phones_found": phones,
            "social_links": socials,
            "forms_found": forms_found,
        }

    except Exception as exc:
        return {
            "scraped": False,
            "error": str(exc),
        }


# -----------------------------
# Public function used by app.py
# -----------------------------
def build_profile_from_lead_row(row) -> Dict[str, Any]:
    """
    Expects sqlite3.Row with columns:
    email, role, budget, cadence, recent, website, business, link, name, etc.
    Returns dict used by profile.html / quote.html.
    """
    lead_email = _clean(row.get("email") if hasattr(row, "get") else row["email"])
    website = _clean(row.get("website") if hasattr(row, "get") else row["website"])
    business_name = _clean(
        row.get("business") if hasattr(row, "get") else row["business"]
    )
    link = _clean(row.get("link") if hasattr(row, "get") else row["link"])
    role = _clean(row.get("role") if hasattr(row, "get") else row["role"])
    budget = _clean(row.get("budget") if hasattr(row, "get") else row["budget"])
    cadence = _clean(row.get("cadence") if hasattr(row, "get") else row["cadence"])
    recent = _clean(row.get("recent") if hasattr(row, "get") else row["recent"])

    scraped = (
        _scrape_website(website)
        if website
        else {"scraped": False, "error": "No website"}
    )

    industry = scraped.get("industry") or "Service business"
    services = scraped.get("services_found") or []

    signals = {
        "services_found": services,
        "phones_found": scraped.get("phones_found") or [],
        "social_links": scraped.get("social_links") or [],
        "forms_found": bool(scraped.get("forms_found")),
        "final_url": scraped.get("final_url") or website,
        "domain": _domain(scraped.get("final_url") or website),
    }

    bottlenecks = _generate_bottlenecks(industry, signals)
    money_levers, time_levers, quick_wins, risks = _generate_levers(
        industry, services, signals
    )

    # Offer ideas: make them vary by service + industry
    offer_ideas = []
    if services:
        offer_ideas.append(
            f"{services[0]} — ‘Same-day response’ offer with tracked calls + form."
        )
        if len(services) > 1:
            offer_ideas.append(
                f"{services[1]} — ‘Free assessment / quote’ funnel with instant follow-up."
            )
    else:
        offer_ideas.append(
            "One hero offer + one backup offer — both tracked by channel."
        )

    profile = {
        "lead": {
            "email": lead_email,
            "role": role or "Not provided",
            "budget": budget or "Not provided",
            "cadence": cadence or "Not provided",
            "recent": recent or "Not provided",
            "website": website or "Not provided",
            "business": business_name or "Not provided",
            "link": link or "",
        },
        "business": {
            "name": business_name or (scraped.get("website_title") or "Not provided"),
            "industry": industry,
            "location": "Not provided",
            "website_title": scraped.get("website_title") or "",
            "website_description": scraped.get("website_description") or "",
            "scraped": bool(scraped.get("scraped")),
            "final_url": scraped.get("final_url") or website,
            "domain": signals["domain"] or "",
        },
        "signals": signals,
        "services": services,
        "offer_ideas": _uniq(offer_ideas)[:3],
        "bottlenecks": bottlenecks,
        "money_levers": money_levers,
        "time_levers": time_levers,
        "quick_wins": quick_wins,
        "risks": risks,
        "debug": {
            "scrape_error": scraped.get("error") if not scraped.get("scraped") else "",
        },
    }

    return profile
