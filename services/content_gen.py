"""
services/content_gen.py — Content Generation Agent for CHKD

Weekly pipeline (runs after job_social_intelligence):
  1. scrape_app_context()         — scrape getchkd.app + pull live Supabase stats
  2. generate_content_plans()     — Claude sonnet-4-6 writes 10 pieces (copy, captions, hashtags)
  3. render_content_piece()       — Playwright + HTML templates → PNG bytes per slide
  4. run_weekly_content_gen()     — orchestrates pipeline, saves to DB, emails Tommy

All images stored as base64 JSON arrays in content_pieces.image_data.
No filesystem dependency — works on Railway's ephemeral fs.

Required env vars:
  ANTHROPIC_API_KEY   — for Claude copy generation
  SUPABASE_URL / SUPABASE_SERVICE_KEY — for live stats

Playwright uses the system Chromium installed via nixpacks.toml (Railway).
Locally, Playwright uses its bundled Chromium (run: playwright install chromium).
"""

import base64
import json
import os
import shutil
import tempfile
import textwrap
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
from bs4 import BeautifulSoup

from services.email import send_email

# ── Constants ─────────────────────────────────────────────────────────────────

TOMMY_EMAIL      = os.getenv("TOMMY_EMAIL", "tomcampos1@aol.com")
SUPABASE_URL     = os.getenv("SUPABASE_URL", "https://vmpoexkcdcsbufqxwdwe.supabase.co")
SUPABASE_KEY     = os.getenv("SUPABASE_SERVICE_KEY", "")
CHKD_APP_URL     = "https://getchkd.app"

# Brand palette
_BG      = "#0a0a0a"
_WHITE   = "#ffffff"
_ACCENT  = "#00E5FF"
_MUTED   = "#888888"
_FONT    = "'Barlow Condensed', Impact, 'Arial Narrow', Arial, sans-serif"
_GFONT   = "https://fonts.googleapis.com/css2?family=Barlow+Condensed:ital,wght@0,400;0,600;0,700;0,900;1,400&display=swap"


# ── 1. App context scraper ────────────────────────────────────────────────────

def scrape_app_context() -> Dict[str, Any]:
    """Scrape getchkd.app for brand copy + pull live Supabase stats."""
    ctx: Dict[str, Any] = {
        "app_name": "CHKD",
        "app_url": CHKD_APP_URL,
        "features": [],
        "key_copy": [],
        "ctas": [],
    }

    try:
        r = httpx.get(CHKD_APP_URL, timeout=15, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0 (compatible; DudingBot/1.0)"})
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "lxml")
            for tag in ["h1", "h2", "h3"]:
                ctx["key_copy"].extend(
                    t.get_text(strip=True)
                    for t in soup.find_all(tag)
                    if t.get_text(strip=True)
                )
            ctx["ctas"] = [
                a.get_text(strip=True)
                for a in soup.find_all("a")
                if a.get_text(strip=True) and len(a.get_text(strip=True)) < 60
            ][:10]
    except Exception as exc:
        print(f"[content_gen] scrape_app_context failed: {exc}")

    ctx["stats"] = _get_supabase_stats()
    return ctx


def _get_supabase_stats() -> Dict[str, Any]:
    """Pull live counts from CHKD Supabase: users, check-ins, active streaks, highest streak."""
    stats = {"total_users": 0, "total_checkins": 0, "active_streaks": 0, "highest_streak": 0}
    if not SUPABASE_KEY:
        return stats

    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Prefer": "count=exact",
    }

    try:
        # Total users
        r = httpx.get(f"{SUPABASE_URL}/rest/v1/profiles", headers=headers,
                      params={"select": "id", "limit": "1"}, timeout=10)
        if r.status_code == 200:
            ct = r.headers.get("content-range", "")
            stats["total_users"] = int(ct.split("/")[-1]) if "/" in ct else len(r.json())
    except Exception:
        pass

    try:
        # Total check-ins (days with score > 0)
        r = httpx.get(f"{SUPABASE_URL}/rest/v1/days", headers=headers,
                      params={"select": "id", "score": "gt.0", "limit": "1"}, timeout=10)
        if r.status_code == 200:
            ct = r.headers.get("content-range", "")
            stats["total_checkins"] = int(ct.split("/")[-1]) if "/" in ct else len(r.json())
    except Exception:
        pass

    try:
        # Days data for streak computation (last 30 days)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).date().isoformat()
        r = httpx.get(f"{SUPABASE_URL}/rest/v1/days",
                      headers={**headers, "Prefer": ""},
                      params={"select": "user_id,score,date", "date": f"gte.{cutoff}", "limit": "10000"},
                      timeout=15)
        if r.status_code == 200:
            rows = r.json() or []
            days_by_user: Dict[str, List[Dict]] = {}
            for row in rows:
                uid = row.get("user_id")
                if uid:
                    days_by_user.setdefault(uid, []).append(row)

            today = datetime.now(timezone.utc).date()
            active = 0
            highest = 0
            for uid, user_rows in days_by_user.items():
                scored = {_parse_date(r.get("date")) for r in user_rows if (r.get("score") or 0) > 0}
                scored.discard(None)
                # Active in last 7 days
                if any((today - timedelta(days=i)) in scored for i in range(7)):
                    active += 1
                # Streak length ending today (or recently)
                streak = 0
                for i in range(30):
                    if (today - timedelta(days=i)) in scored:
                        streak += 1
                    else:
                        break
                highest = max(highest, streak)

            stats["active_streaks"] = active
            stats["highest_streak"] = highest
    except Exception as exc:
        print(f"[content_gen] streak stats failed: {exc}")

    return stats


def _parse_date(val: Any) -> Optional[date]:
    if not val:
        return None
    try:
        return date.fromisoformat(str(val)[:10])
    except ValueError:
        return None


# ── 2. Claude content planner ─────────────────────────────────────────────────

def generate_content_plans(
    intel_report: Dict[str, Any],
    app_context: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Calls Claude to generate 10 content pieces combining intel + app context.
    Returns a list of piece specs. Falls back to stub specs if API key missing.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("[content_gen] ANTHROPIC_API_KEY not set — using stub content plans")
        return _stub_content_plans(intel_report, app_context)

    stats   = app_context.get("stats", {})
    hooks   = intel_report.get("top_hooks", [])
    themes  = intel_report.get("winning_themes", [])
    angles  = intel_report.get("ad_angles", [])
    words   = intel_report.get("power_words", [])

    stats_block = (
        f"- Total users: {stats.get('total_users', 'N/A')}\n"
        f"- Total check-ins logged: {stats.get('total_checkins', 'N/A')}\n"
        f"- Users active in last 7 days: {stats.get('active_streaks', 'N/A')}\n"
        f"- Highest current streak: {stats.get('highest_streak', 'N/A')} days"
    )
    hooks_block  = "\n".join(f"- {h}" for h in hooks[:5])
    themes_block = "\n".join(f"- {t['theme']}: {t['why']}" for t in themes[:3])
    angles_block = "\n".join(f"- {a['headline']} | {a['first_line']}" for a in angles[:3])
    words_block  = ", ".join(words[:20])

    prompt = textwrap.dedent(f"""
        You are a social media content strategist for CHKD — a daily check-in app for men
        that tracks 5 personal non-negotiables (sleep, movement, nutrition, mindset, discipline).
        Brand voice: direct, masculine, no fluff, accountability-focused. Short sentences. No emojis.

        LIVE APP STATS:
        {stats_block}

        THIS WEEK'S TOP HOOKS (from competitor research):
        {hooks_block}

        WINNING THEMES:
        {themes_block}

        AD ANGLES:
        {angles_block}

        POWER WORDS from real comments:
        {words_block}

        Generate exactly 10 Instagram content pieces for CHKD. Mix these types:
        - 2 quote_card: single-slide, bold hook adapted for CHKD audience
        - 2 stat_card: single-slide, real CHKD stat with short punchy context
        - 1 feature_carousel: 5 slides showing CHKD's key features with benefit copy
        - 2 educational_carousel: 5-7 slides, storytelling format (e.g. "Why men quit at Day 14")
        - 1 power_words_card: single-slide, 12-15 power words as bold text grid
        - 2 ad_angle_card: single-slide, one ad angle each — headline + short copy block

        For each piece, generate:
        - content_type (exact string from list above)
        - title (short internal label, 5 words max)
        - slides: array of slide objects, each with:
            - headline (UPPERCASE, max 8 words)
            - body (1-3 sentences for carousels, null for single-image types)
            - words (array of strings, only for power_words_card)
        - caption (full Instagram caption, 3-5 short paragraphs, line breaks between, ends with CTA to {CHKD_APP_URL})
        - hashtags (array of 8-12 relevant hashtags as strings with #)

        Return ONLY valid JSON: {{"pieces": [...]}}. No markdown, no explanation.
    """).strip()

    try:
        import anthropic
        client  = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8192,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        if raw.startswith("```"):
            parts = raw.split("```")
            raw   = parts[1][4:].strip() if parts[1].startswith("json") else parts[1].strip()
        data = json.loads(raw)
        pieces = data.get("pieces", [])
        print(f"[content_gen] Claude generated {len(pieces)} content pieces")
        return pieces
    except Exception as exc:
        print(f"[content_gen] Claude call failed: {exc}")
        return _stub_content_plans(intel_report, app_context)


def _stub_content_plans(intel: Dict, ctx: Dict) -> List[Dict]:
    hooks  = intel.get("top_hooks", ["Stop telling people your goals. Go dark."])
    words  = intel.get("power_words", ["discipline", "locked in", "no excuses", "accountability",
                                        "earned it", "the grind", "streak", "showing up",
                                        "built different", "the work", "own it", "standards"])
    stats  = ctx.get("stats", {})
    users  = stats.get("total_users", 247)
    streak = stats.get("highest_streak", 23)
    active = stats.get("active_streaks", 89)

    return [
        {
            "content_type": "quote_card",
            "title": "Go dark",
            "slides": [{"headline": hooks[0].upper(), "body": None}],
            "caption": "The men who are actually building something aren't posting about it.\n\nThey're showing up. Every day. Scoring their non-negotiables.\n\nWhat are your 5?\n\ngetchkd.app",
            "hashtags": ["#discipline", "#accountability", "#mensdiscipline", "#75hard", "#CHKD", "#buildingempires", "#nodaysoff", "#mentalstrength"],
        },
        {
            "content_type": "quote_card",
            "title": "Control yourself",
            "slides": [{"headline": "A MAN WHO CONTROLS HIMSELF CONTROLS EVERYTHING.", "body": None}],
            "caption": "It starts with 5 habits. Every day. Without exception.\n\nNot when you feel like it. Every. Day.\n\nTrack them at getchkd.app",
            "hashtags": ["#discipline", "#accountability", "#selfcontrol", "#mensmindset", "#CHKD", "#dailyhabits", "#consistency", "#growthmindset"],
        },
        {
            "content_type": "stat_card",
            "title": "Active streaks",
            "slides": [{"headline": str(active), "body": f"men currently on an active streak", "subtext": "It grows every day."}],
            "caption": f"{active} men logged in yesterday.\n\nNot because they were motivated.\n\nBecause they built a system.\n\nJoin them: getchkd.app",
            "hashtags": ["#accountability", "#discipline", "#streak", "#CHKD", "#buildasystem", "#showingup", "#consistency", "#mensdiscipline"],
        },
        {
            "content_type": "stat_card",
            "title": "Highest streak",
            "slides": [{"headline": f"{streak} DAYS", "body": "Highest current streak in the app", "subtext": "Who's chasing it?"}],
            "caption": f"Someone in CHKD is on a {streak}-day streak right now.\n\nNot 75 HARD. Not a challenge with an end date.\n\nJust showing up. Every single day.\n\nWhat's your number? getchkd.app",
            "hashtags": ["#streak", "#discipline", "#nodaysoff", "#CHKD", "#accountability", "#consistency", "#dailyhabits", "#mentalstrength"],
        },
        {
            "content_type": "feature_carousel",
            "title": "App features",
            "slides": [
                {"headline": "CHECK IN. EVERY DAY.", "body": "5 non-negotiables. Score each one 0-10. Takes 60 seconds. Changes everything."},
                {"headline": "YOUR DAILY SCORE", "body": "See exactly where you stood up and where you folded. The number doesn't lie."},
                {"headline": "STREAKS THAT MEAN SOMETHING", "body": "Not a badge. Not a notification. A record of who you actually are."},
                {"headline": "COACH MODE", "body": "Share your check-ins with someone who holds you to your word. Accountability isn't optional."},
                {"headline": "START TODAY", "body": f"Join {users}+ men already checking in daily. Free. No BS.\n\ngetchkd.app"},
            ],
            "caption": "Your 5 non-negotiables. Tracked daily. No excuses.\n\nThis is what CHKD is built for.\n\nSwipe to see how it works →\n\nStart your check-in streak at getchkd.app",
            "hashtags": ["#CHKD", "#dailycheckin", "#accountability", "#discipline", "#habittracker", "#mensapp", "#nonegotiables", "#buildhabits"],
        },
        {
            "content_type": "educational_carousel",
            "title": "Why men quit at Day 14",
            "slides": [
                {"headline": "MOST MEN QUIT BEFORE DAY 14.", "body": "Here's exactly why — and how to make sure you don't."},
                {"headline": "DAY 1-3: THE DOPAMINE SPIKE.", "body": "Starting something new feels good. Your brain rewards it. You're fired up."},
                {"headline": "DAY 7: THE FIRST REAL TEST.", "body": "Life gets in the way. You miss one day. You tell yourself you'll make up for it. You don't."},
                {"headline": "DAY 14: THE CLIFF.", "body": "The motivation is gone. The habit isn't built yet. This is where 80% of men stop."},
                {"headline": "THE FIX: A DAILY SYSTEM.", "body": "Motivation fades. Systems don't. Score your 5 non-negotiables every single night. Let the number hold you accountable."},
                {"headline": "DON'T QUIT ON DAY 14.", "body": f"Join the men who didn't. getchkd.app", "is_cta": True},
            ],
            "caption": "Most men quit before Day 14. I've seen it hundreds of times.\n\nIt's not willpower. It's the absence of a system.\n\nSwipe to see exactly how it happens — and how to beat it.\n\ngetchkd.app",
            "hashtags": ["#discipline", "#habits", "#accountability", "#mensdiscipline", "#CHKD", "#selfimprovement", "#growthmindset", "#75hard"],
        },
        {
            "content_type": "educational_carousel",
            "title": "5 non-negotiables explained",
            "slides": [
                {"headline": "THE 5 NON-NEGOTIABLES.", "body": "Not goals. Not aspirations. The 5 things that, when you do them, make everything else better."},
                {"headline": "#1: SLEEP.", "body": "7-9 hours. Non-negotiable. Every other habit depends on this one."},
                {"headline": "#2: MOVEMENT.", "body": "Doesn't have to be the gym. Move your body with intention every single day."},
                {"headline": "#3: NUTRITION.", "body": "Eat like the person you want to become. Not perfectly — intentionally."},
                {"headline": "#4: MINDSET WORK.", "body": "Read. Journal. Meditate. Something that sharpens the mind, daily."},
                {"headline": "#5: YOUR PERSONAL NON-NEGOTIABLE.", "body": "Everyone has one. The thing that changes your entire life when you're consistent with it. What's yours?"},
                {"headline": "SCORE ALL 5 TONIGHT.", "body": "getchkd.app", "is_cta": True},
            ],
            "caption": "The 5 non-negotiables aren't a list of habits.\n\nThey're the foundation.\n\nGet all 5 right consistently and watch every other area of your life follow.\n\nTrack yours daily at getchkd.app",
            "hashtags": ["#nonegotiables", "#5habits", "#discipline", "#accountability", "#CHKD", "#mensdiscipline", "#dailyhabits", "#buildasystem"],
        },
        {
            "content_type": "power_words_card",
            "title": "Power words",
            "slides": [{"headline": "WORDS MEN WHO SHOW UP USE.", "words": words[:15], "body": None}],
            "caption": "These aren't marketing words.\n\nThey're what men say in the comments when they're actually in it.\n\nAre you in it? getchkd.app",
            "hashtags": ["#discipline", "#accountability", "#mensdiscipline", "#CHKD", "#lockedin", "#showingup", "#consistency", "#mentalstrength"],
        },
        {
            "content_type": "ad_angle_card",
            "title": "System not motivation",
            "slides": [{"headline": "5 HABITS. EVERY DAY. NO EXCEPTIONS.", "body": "Stop relying on motivation. CHKD gives you a daily scorecard that holds you to your non-negotiables — no matter how you feel."}],
            "caption": "Motivation is a feeling. Feelings change.\n\nSystems don't.\n\nYour 5 non-negotiables. Tracked daily. getchkd.app",
            "hashtags": ["#CHKD", "#discipline", "#buildasystem", "#accountability", "#mensdiscipline", "#habittracker", "#nodaysoff", "#showingup"],
        },
        {
            "content_type": "ad_angle_card",
            "title": "See your discipline",
            "slides": [{"headline": "WHAT IF YOU COULD SEE YOUR DISCIPLINE?", "body": "Most men know what they should do. CHKD is the daily check-in that makes sure you actually do it — and shows you the truth every single day."}],
            "caption": "The number doesn't lie.\n\nScore your 5 non-negotiables tonight and see exactly where you actually stand.\n\ngetchkd.app",
            "hashtags": ["#CHKD", "#accountability", "#discipline", "#selfreflection", "#mensdiscipline", "#dailyscore", "#consistency", "#buildasystem"],
        },
    ]


# ── 3. HTML templates ──────────────────────────────────────────────────────────

def _base_html(body_content: str, extra_style: str = "") -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="{_GFONT}" rel="stylesheet">
<style>
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
html, body {{ width: 1080px; height: 1080px; overflow: hidden; }}
body {{
  background: {_BG};
  font-family: {_FONT};
  color: {_WHITE};
  position: relative;
}}
.logo {{
  position: absolute;
  bottom: 48px;
  right: 56px;
  font-family: {_FONT};
  font-size: 20px;
  font-weight: 900;
  color: {_WHITE};
  letter-spacing: 0.25em;
  text-transform: uppercase;
  opacity: 0.45;
}}
.accent-bar {{
  position: absolute;
  top: 0;
  left: 0;
  width: 8px;
  height: 240px;
  background: {_ACCENT};
}}
{extra_style}
</style>
</head>
<body>
{body_content}
<div class="logo">CHKD</div>
</body>
</html>"""


def _html_quote_card(headline: str) -> str:
    # Wrap long lines (Barlow Condensed is wide at 80px+)
    words    = headline.split()
    lines    = []
    line     = []
    for w in words:
        line.append(w)
        if len(" ".join(line)) > 22:
            lines.append(" ".join(line[:-1]))
            line = [w]
    if line:
        lines.append(" ".join(line))
    wrapped = "<br>".join(lines) if lines else headline

    body = f"""
<div class="accent-bar"></div>
<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;width:100%;height:100%;padding:90px 80px;">
  <div style="width:60px;height:5px;background:{_ACCENT};margin-bottom:48px;"></div>
  <div style="font-size:88px;font-weight:900;line-height:0.92;text-align:center;text-transform:uppercase;letter-spacing:-0.01em;">{wrapped}</div>
  <div style="width:60px;height:5px;background:{_ACCENT};margin-top:48px;"></div>
</div>"""
    return _base_html(body)


def _html_stat_card(headline: str, body: str, subtext: str = None) -> str:
    sub_html = f'<div style="font-size:28px;font-weight:600;color:{_MUTED};margin-top:16px;text-transform:uppercase;letter-spacing:0.12em;">{subtext}</div>' if subtext else ""
    body_html = f"""
<div class="accent-bar"></div>
<div style="display:flex;flex-direction:column;align-items:center;justify-content:center;width:100%;height:100%;padding:90px 80px;">
  <div style="font-size:220px;font-weight:900;line-height:0.85;color:{_ACCENT};letter-spacing:-0.04em;">{headline}</div>
  <div style="font-size:42px;font-weight:700;color:{_WHITE};margin-top:24px;text-align:center;text-transform:uppercase;letter-spacing:0.06em;">{body}</div>
  {sub_html}
</div>"""
    return _base_html(body_html)


def _html_carousel_slide(
    slide_num: int,
    total_slides: int,
    headline: str,
    body: str,
    is_cta: bool = False,
) -> str:
    progress_bars = "".join(
        f'<div style="flex:1;height:3px;border-radius:2px;background:{"#fff" if i < slide_num else "#333"};margin:0 2px;"></div>'
        for i in range(1, total_slides + 1)
    )
    slide_label = f"{slide_num:02d}/{total_slides:02d}"
    cta_style   = f"color:{_ACCENT};" if is_cta else ""
    body_size   = "36px" if len(body) < 80 else "30px"

    body_html = f"""
<div class="accent-bar"></div>
<div style="padding:64px 72px;height:100%;display:flex;flex-direction:column;justify-content:space-between;">
  <div style="display:flex;justify-content:space-between;align-items:center;">
    <div style="display:flex;gap:4px;flex:1;">{progress_bars}</div>
    <div style="font-size:18px;font-weight:700;color:{_MUTED};margin-left:16px;letter-spacing:0.08em;">{slide_label}</div>
  </div>

  <div style="flex:1;display:flex;flex-direction:column;justify-content:center;padding:48px 0;">
    <div style="font-size:72px;font-weight:900;line-height:0.9;text-transform:uppercase;letter-spacing:-0.01em;{cta_style}">{headline}</div>
    <div style="width:48px;height:4px;background:{_ACCENT};margin:32px 0;"></div>
    <div style="font-size:{body_size};font-weight:600;color:{"#ccc" if not is_cta else _WHITE};line-height:1.4;">{body}</div>
  </div>

  <div style="font-size:16px;font-weight:700;color:{_MUTED};letter-spacing:0.2em;text-transform:uppercase;">getchkd.app</div>
</div>"""
    return _base_html(body_html)


def _html_power_words_card(words: List[str]) -> str:
    # Arrange words in a staggered grid — larger font for first 4
    cells = []
    for i, w in enumerate(words[:15]):
        size  = "56px" if i < 4 else "38px" if i < 9 else "30px"
        color = _ACCENT if i < 2 else _WHITE if i < 7 else _MUTED
        cells.append(
            f'<div style="font-size:{size};font-weight:900;color:{color};text-transform:uppercase;'
            f'line-height:1.1;letter-spacing:0.04em;padding:6px 0;">{w}</div>'
        )

    body_html = f"""
<div class="accent-bar"></div>
<div style="padding:72px 80px;height:100%;display:flex;flex-direction:column;justify-content:space-between;">
  <div>
    <div style="font-size:18px;font-weight:700;color:{_MUTED};letter-spacing:0.25em;text-transform:uppercase;margin-bottom:40px;">THIS WEEK'S POWER WORDS</div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:0 24px;">
      {''.join(cells)}
    </div>
  </div>
  <div style="font-size:16px;font-weight:700;color:{_MUTED};letter-spacing:0.2em;text-transform:uppercase;">getchkd.app</div>
</div>"""
    return _base_html(body_html)


def _html_ad_angle_card(headline: str, body_text: str) -> str:
    # Split headline to fit width
    words   = headline.split()
    line1   = " ".join(words[: max(1, len(words) // 2)])
    line2   = " ".join(words[max(1, len(words) // 2) :])

    body_html = f"""
<div style="position:absolute;inset:0;background:linear-gradient(135deg,{_BG} 60%,#0f1a1a);"></div>
<div class="accent-bar"></div>
<div style="position:relative;z-index:1;padding:80px;height:100%;display:flex;flex-direction:column;justify-content:center;gap:32px;">
  <div style="font-size:88px;font-weight:900;text-transform:uppercase;line-height:0.88;letter-spacing:-0.02em;">{line1}<br><span style="color:{_ACCENT};">{line2}</span></div>
  <div style="width:60px;height:4px;background:{_ACCENT};"></div>
  <div style="font-size:32px;font-weight:600;color:#ccc;line-height:1.4;max-width:800px;">{body_text}</div>
  <div style="font-size:20px;font-weight:700;color:{_MUTED};letter-spacing:0.15em;text-transform:uppercase;margin-top:16px;">getchkd.app</div>
</div>"""
    return _base_html(body_html)


# ── 4. Playwright renderer ─────────────────────────────────────────────────────

def _get_chromium_path() -> Optional[str]:
    """Detect system Chromium (installed via nixpacks.toml on Railway)."""
    for name in ("chromium", "chromium-browser", "google-chrome-stable", "google-chrome"):
        p = shutil.which(name)
        if p:
            return p
    return None


def _render_html_to_png(html: str, width: int = 1080, height: int = 1080) -> Optional[bytes]:
    """
    Render HTML string to PNG bytes via Playwright.
    Handles both sync (APScheduler) and async (FastAPI BackgroundTasks) contexts.
    """
    import concurrent.futures
    import asyncio

    try:
        asyncio.get_running_loop()
        # Running inside an event loop — dispatch to a thread
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(_playwright_render, html, width, height).result(timeout=90)
    except RuntimeError:
        # No event loop — direct call
        return _playwright_render(html, width, height)


def _playwright_render(html: str, width: int, height: int) -> Optional[bytes]:
    try:
        from playwright.sync_api import sync_playwright

        chromium_path = _get_chromium_path()
        launch_args   = [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--single-process",
        ]
        launch_kwargs: Dict[str, Any] = {"args": launch_args}
        if chromium_path:
            launch_kwargs["executable_path"] = chromium_path

        with sync_playwright() as pw:
            browser = pw.chromium.launch(**launch_kwargs)
            page    = browser.new_page(viewport={"width": width, "height": height})
            page.set_content(html, wait_until="networkidle")
            png = page.screenshot(
                clip={"x": 0, "y": 0, "width": width, "height": height},
                type="png",
            )
            browser.close()
            return png
    except Exception as exc:
        print(f"[content_gen] Playwright render failed: {exc}")
        return None


def _png_to_b64(png: bytes) -> str:
    return base64.b64encode(png).decode("utf-8")


# ── 5. Piece renderer (template dispatch) ─────────────────────────────────────

def render_piece(piece: Dict[str, Any]) -> List[str]:
    """
    Render all slides for a content piece.
    Returns list of base64 PNG strings (one per slide).
    Returns empty list on render failure.
    """
    ctype  = piece.get("content_type", "")
    slides = piece.get("slides", [])
    pngs: List[str] = []

    try:
        if ctype == "quote_card":
            headline = (slides[0].get("headline") or "").strip() if slides else ""
            png = _render_html_to_png(_html_quote_card(headline))
            if png:
                pngs.append(_png_to_b64(png))

        elif ctype == "stat_card":
            s   = slides[0] if slides else {}
            png = _render_html_to_png(_html_stat_card(
                s.get("headline", ""),
                s.get("body", ""),
                s.get("subtext"),
            ))
            if png:
                pngs.append(_png_to_b64(png))

        elif ctype in ("feature_carousel", "educational_carousel"):
            total = len(slides)
            for i, s in enumerate(slides, 1):
                png = _render_html_to_png(_html_carousel_slide(
                    i, total,
                    s.get("headline", ""),
                    s.get("body", ""),
                    s.get("is_cta", False),
                ))
                if png:
                    pngs.append(_png_to_b64(png))

        elif ctype == "power_words_card":
            words = slides[0].get("words", []) if slides else []
            png   = _render_html_to_png(_html_power_words_card(words))
            if png:
                pngs.append(_png_to_b64(png))

        elif ctype == "ad_angle_card":
            s   = slides[0] if slides else {}
            png = _render_html_to_png(_html_ad_angle_card(
                s.get("headline", ""),
                s.get("body", ""),
            ))
            if png:
                pngs.append(_png_to_b64(png))

    except Exception as exc:
        print(f"[content_gen] render_piece failed for {ctype}: {exc}")

    return pngs


# ── 6. Email summary ───────────────────────────────────────────────────────────

def build_content_email(pieces: List[Dict], week_of: date, client_id: int) -> Tuple[str, str]:
    week_str = week_of.strftime("%b %d, %Y")
    subject  = f"CHKD Content Ready — Week of {week_str}"

    counts: Dict[str, int] = {}
    for p in pieces:
        ct = p.get("content_type", "unknown")
        counts[ct] = counts.get(ct, 0) + 1

    # Best piece = first approved ad_angle or educational_carousel
    best = next(
        (p for p in pieces if p.get("content_type") == "educational_carousel"), pieces[0] if pieces else None
    )

    counts_str = "\n".join(f"  • {k.replace('_', ' ')}: {v}" for k, v in counts.items())
    best_str   = f"""
TOP RECOMMENDED POST:
  {best.get('title', '—')} ({best.get('content_type', '')})
  Caption preview: {(best.get('caption') or '')[:200]}...
""" if best else ""

    body = f"""CHKD Content Generation — Week of {week_str}

{len(pieces)} graphics generated and ready for review.

BREAKDOWN:
{counts_str}
{best_str}
Review and approve: https://duding.ai/dashboard/clients/{client_id}?tab=content

Tommy (Duding.ai)
"""
    return subject, body


# ── 7. Full pipeline ───────────────────────────────────────────────────────────

def run_weekly_content_gen(client_id: int, intel_report: Optional[Dict] = None) -> int:
    """
    Full content generation pipeline.
    intel_report: pass the analysis dict from run_weekly_intelligence() or None to use stub.
    Returns count of pieces saved.
    """
    from db import SessionLocal
    from models.content_piece import ContentPiece

    today   = datetime.now(timezone.utc).date()
    week_of = today - timedelta(days=today.weekday())

    print(f"[content_gen] Starting — client_id={client_id}, week_of={week_of}")

    # 1. App context + stats
    app_ctx = scrape_app_context()
    print(f"[content_gen] App stats: {app_ctx.get('stats', {})}")

    # 2. Content plans
    plans = generate_content_plans(intel_report or {}, app_ctx)
    if not plans:
        print("[content_gen] No content plans — aborting")
        return 0

    # 3. Render each piece
    db = SessionLocal()
    saved = 0
    try:
        for plan in plans:
            ctype    = plan.get("content_type", "unknown")
            title    = plan.get("title", ctype)
            caption  = plan.get("caption", "")
            hashtags = json.dumps(plan.get("hashtags", []))

            print(f"[content_gen] Rendering: {title} ({ctype})")
            b64_images = render_piece(plan)
            status     = "draft" if b64_images else "render_failed"

            # Upsert by client + week + title
            existing = db.query(ContentPiece).filter(
                ContentPiece.client_id == client_id,
                ContentPiece.week_of   == week_of,
                ContentPiece.title     == title,
            ).first()

            if existing:
                existing.image_data  = json.dumps(b64_images) if b64_images else None
                existing.caption     = caption
                existing.hashtags    = hashtags
                existing.slide_count = len(b64_images)
                existing.status      = status
            else:
                db.add(ContentPiece(
                    client_id   = client_id,
                    week_of     = week_of,
                    content_type = ctype,
                    title        = title,
                    caption      = caption,
                    hashtags     = hashtags,
                    image_data   = json.dumps(b64_images) if b64_images else None,
                    slide_count  = len(b64_images),
                    status       = status,
                ))
            db.commit()
            saved += 1
            print(f"[content_gen] Saved: {title} — {len(b64_images)} slides, status={status}")

    except Exception as exc:
        db.rollback()
        print(f"[content_gen] ERROR saving pieces: {exc}")
    finally:
        db.close()

    # 4. Email Tommy
    try:
        pieces_for_email = [
            {"title": p.get("title"), "content_type": p.get("content_type"), "caption": p.get("caption")}
            for p in plans
        ]
        subject, body = build_content_email(pieces_for_email, week_of, client_id)
        send_email(TOMMY_EMAIL, subject, body, from_name="Duding")
        print(f"[content_gen] Email sent to {TOMMY_EMAIL}")
    except Exception as exc:
        print(f"[content_gen] Email failed: {exc}")

    print(f"[content_gen] Done — {saved} pieces saved")
    return saved
