"""
services/social_intelligence.py — Social Intelligence Agent for CHKD

Weekly pipeline:
  1. scrape_competitor_data()  — pull top posts per competitor (mock now, Apify later)
  2. analyze_with_claude()     — Claude sonnet-4-6 returns structured JSON insights
  3. run_weekly_intelligence() — orchestrates the full pipeline, saves to DB, emails Tommy

To activate real Apify scraping:
  - Set APIFY_API_KEY env var on Railway
  - In scrape_competitor_data(), change _fetch_mock() → _fetch_apify()
  - That's it — everything else is already wired.

Required env vars:
  ANTHROPIC_API_KEY   — for Claude analysis
  APIFY_API_KEY       — (future) for real scraping
"""
import json
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List

import httpx

from services.email import send_email

TOMMY_EMAIL = os.getenv("TOMMY_EMAIL", "tomcampos1@aol.com")
APIFY_API_KEY = os.getenv("APIFY_API_KEY", "")

# ── Competitor target list ────────────────────────────────────────────────────

COMPETITORS = [
    {"platform": "tiktok", "handle": "davidgoggins",    "display_name": "David Goggins"},
    {"platform": "tiktok", "handle": "andyfrisella",    "display_name": "Andy Frisella"},
    {"platform": "tiktok", "handle": "atomichabits",    "display_name": "Atomic Habits"},
    {"platform": "tiktok", "handle": "75hardofficial",  "display_name": "75 HARD Official"},
    {"platform": "tiktok", "handle": "mensdiscipline",  "display_name": "Men's Discipline"},
]

# ── Mock data (Apify-shaped) ──────────────────────────────────────────────────
# Structure matches the Apify TikTok Scraper (actor: clockworks/free-tiktok-scraper)
# Each post object includes the fields we care about. When Apify goes live, _fetch_apify()
# returns the same shape so nothing downstream changes.

_MOCK_POSTS: List[Dict[str, Any]] = [
    # ── David Goggins ─────────────────────────────────────────────────────────
    {
        "id": "7380001001",
        "text": "Who's going to carry the boats? You are not special. You are not a beautiful unique snowflake. The only way out is through.",
        "createTime": 1719792000,
        "authorMeta": {"name": "davidgoggins", "nickName": "David Goggins"},
        "stats": {"playCount": 3200000, "diggCount": 189000, "commentCount": 8700, "shareCount": 42000, "collectCount": 61000},
        "isVideo": True, "contentType": "reel",
        "webVideoUrl": "https://www.tiktok.com/@davidgoggins/video/7380001001",
    },
    {
        "id": "7380001002",
        "text": "The most dangerous person on earth is the person who has learned to handle pain. Most of you will never find out what you're made of.",
        "createTime": 1719705600,
        "authorMeta": {"name": "davidgoggins", "nickName": "David Goggins"},
        "stats": {"playCount": 2100000, "diggCount": 121000, "commentCount": 5200, "shareCount": 29000, "collectCount": 44000},
        "isVideo": True, "contentType": "reel",
        "webVideoUrl": "https://www.tiktok.com/@davidgoggins/video/7380001002",
    },
    {
        "id": "7380001003",
        "text": "Stop telling people your goals. Go dark. Go quiet. Let the work speak. Nobody needs to know what you're building.",
        "createTime": 1719619200,
        "authorMeta": {"name": "davidgoggins", "nickName": "David Goggins"},
        "stats": {"playCount": 1800000, "diggCount": 98000, "commentCount": 3900, "shareCount": 21000, "collectCount": 35000},
        "isVideo": True, "contentType": "reel",
        "webVideoUrl": "https://www.tiktok.com/@davidgoggins/video/7380001003",
    },
    {
        "id": "7380001004",
        "text": "Day 1 vs Day 365. The difference isn't talent. It's the days you showed up when you didn't want to.",
        "createTime": 1719532800,
        "authorMeta": {"name": "davidgoggins", "nickName": "David Goggins"},
        "stats": {"playCount": 1400000, "diggCount": 76000, "commentCount": 2900, "shareCount": 16000, "collectCount": 28000},
        "isVideo": False, "contentType": "static",
        "webVideoUrl": "https://www.tiktok.com/@davidgoggins/video/7380001004",
    },
    {
        "id": "7380001005",
        "text": "You have to be willing to be uncomfortable every single day. Comfort is the enemy of growth and growth is the enemy of mediocrity.",
        "createTime": 1719446400,
        "authorMeta": {"name": "davidgoggins", "nickName": "David Goggins"},
        "stats": {"playCount": 960000, "diggCount": 54000, "commentCount": 2100, "shareCount": 11000, "collectCount": 19000},
        "isVideo": True, "contentType": "reel",
        "webVideoUrl": "https://www.tiktok.com/@davidgoggins/video/7380001005",
    },

    # ── Andy Frisella ──────────────────────────────────────────────────────────
    {
        "id": "7380002001",
        "text": "The 75 Hard challenge isn't about fitness. It's about proving to yourself that you can commit to something and follow through. That's the rarest skill in the world right now.",
        "createTime": 1719792000,
        "authorMeta": {"name": "andyfrisella", "nickName": "Andy Frisella"},
        "stats": {"playCount": 1700000, "diggCount": 92000, "commentCount": 4400, "shareCount": 23000, "collectCount": 38000},
        "isVideo": True, "contentType": "reel",
        "webVideoUrl": "https://www.tiktok.com/@andyfrisella/video/7380002001",
    },
    {
        "id": "7380002002",
        "text": "Every single day you don't do what you said you'd do, you're telling your brain you can't be trusted. That's how mediocrity compounds.",
        "createTime": 1719705600,
        "authorMeta": {"name": "andyfrisella", "nickName": "Andy Frisella"},
        "stats": {"playCount": 1300000, "diggCount": 71000, "commentCount": 3100, "shareCount": 17000, "collectCount": 29000},
        "isVideo": True, "contentType": "reel",
        "webVideoUrl": "https://www.tiktok.com/@andyfrisella/video/7380002002",
    },
    {
        "id": "7380002003",
        "text": "The reason you're not where you want to be is a lack of discipline — not a lack of information. You know what to do. Start doing it.",
        "createTime": 1719619200,
        "authorMeta": {"name": "andyfrisella", "nickName": "Andy Frisella"},
        "stats": {"playCount": 1100000, "diggCount": 63000, "commentCount": 2700, "shareCount": 14000, "collectCount": 24000},
        "isVideo": True, "contentType": "reel",
        "webVideoUrl": "https://www.tiktok.com/@andyfrisella/video/7380002003",
    },
    {
        "id": "7380002004",
        "text": "5 things you do every single day that move the needle. Not 50. Not 20. Five. And you protect them like your life depends on it.",
        "createTime": 1719532800,
        "authorMeta": {"name": "andyfrisella", "nickName": "Andy Frisella"},
        "stats": {"playCount": 890000, "diggCount": 48000, "commentCount": 2200, "shareCount": 11000, "collectCount": 19000},
        "isVideo": True, "contentType": "reel",
        "webVideoUrl": "https://www.tiktok.com/@andyfrisella/video/7380002004",
    },
    {
        "id": "7380002005",
        "text": "I've talked to thousands of successful people. Not one of them said the secret was motivation. Every single one said discipline. Every. One.",
        "createTime": 1719446400,
        "authorMeta": {"name": "andyfrisella", "nickName": "Andy Frisella"},
        "stats": {"playCount": 740000, "diggCount": 39000, "commentCount": 1800, "shareCount": 9000, "collectCount": 15000},
        "isVideo": False, "contentType": "static",
        "webVideoUrl": "https://www.tiktok.com/@andyfrisella/video/7380002005",
    },

    # ── Atomic Habits ─────────────────────────────────────────────────────────
    {
        "id": "7380003001",
        "text": "You don't rise to the level of your goals. You fall to the level of your systems. Stop setting goals. Build systems.",
        "createTime": 1719792000,
        "authorMeta": {"name": "atomichabits", "nickName": "Atomic Habits"},
        "stats": {"playCount": 2400000, "diggCount": 134000, "commentCount": 5900, "shareCount": 31000, "collectCount": 52000},
        "isVideo": False, "contentType": "static",
        "webVideoUrl": "https://www.tiktok.com/@atomichabits/video/7380003001",
    },
    {
        "id": "7380003002",
        "text": "A habit is a decision you make once and live with every day. The question is: are you deciding consciously or letting your default decide for you?",
        "createTime": 1719705600,
        "authorMeta": {"name": "atomichabits", "nickName": "Atomic Habits"},
        "stats": {"playCount": 1600000, "diggCount": 88000, "commentCount": 3700, "shareCount": 19000, "collectCount": 33000},
        "isVideo": True, "contentType": "reel",
        "webVideoUrl": "https://www.tiktok.com/@atomichabits/video/7380003002",
    },
    {
        "id": "7380003003",
        "text": "The two-minute rule changed my life. If it takes less than two minutes, do it now. Everything else is just procrastination with extra steps.",
        "createTime": 1719619200,
        "authorMeta": {"name": "atomichabits", "nickName": "Atomic Habits"},
        "stats": {"playCount": 1200000, "diggCount": 67000, "commentCount": 2900, "shareCount": 15000, "collectCount": 26000},
        "isVideo": True, "contentType": "reel",
        "webVideoUrl": "https://www.tiktok.com/@atomichabits/video/7380003003",
    },
    {
        "id": "7380003004",
        "text": "Track your streak. Don't break the chain. Every day you show up adds a link. Missing once is okay. Missing twice is the start of a new habit — a bad one.",
        "createTime": 1719532800,
        "authorMeta": {"name": "atomichabits", "nickName": "Atomic Habits"},
        "stats": {"playCount": 980000, "diggCount": 54000, "commentCount": 2300, "shareCount": 12000, "collectCount": 21000},
        "isVideo": False, "contentType": "static",
        "webVideoUrl": "https://www.tiktok.com/@atomichabits/video/7380003004",
    },
    {
        "id": "7380003005",
        "text": "Identity is the output of your daily habits. Every rep you do is a vote for the person you want to become.",
        "createTime": 1719446400,
        "authorMeta": {"name": "atomichabits", "nickName": "Atomic Habits"},
        "stats": {"playCount": 810000, "diggCount": 44000, "commentCount": 1900, "shareCount": 9500, "collectCount": 17000},
        "isVideo": True, "contentType": "reel",
        "webVideoUrl": "https://www.tiktok.com/@atomichabits/video/7380003005",
    },

    # ── 75 HARD Official ──────────────────────────────────────────────────────
    {
        "id": "7380004001",
        "text": "Day 75. The person who started this isn't the same person finishing it. That's the whole point.",
        "createTime": 1719792000,
        "authorMeta": {"name": "75hardofficial", "nickName": "75 HARD Official"},
        "stats": {"playCount": 1900000, "diggCount": 107000, "commentCount": 4800, "shareCount": 26000, "collectCount": 42000},
        "isVideo": True, "contentType": "reel",
        "webVideoUrl": "https://www.tiktok.com/@75hardofficial/video/7380004001",
    },
    {
        "id": "7380004002",
        "text": "Day 1 check-in: Diet ✓ Workout 1 ✓ Workout 2 ✓ 10 pages ✓ Water ✓ Progress photo ✓ The system works if you work the system.",
        "createTime": 1719705600,
        "authorMeta": {"name": "75hardofficial", "nickName": "75 HARD Official"},
        "stats": {"playCount": 1500000, "diggCount": 82000, "commentCount": 3600, "shareCount": 20000, "collectCount": 34000},
        "isVideo": True, "contentType": "reel",
        "webVideoUrl": "https://www.tiktok.com/@75hardofficial/video/7380004002",
    },
    {
        "id": "7380004003",
        "text": "People ask me what the hardest day is. It's not day 1. Day 1 you're motivated. The hardest day is day 14. The motivation is gone and the habit isn't built yet.",
        "createTime": 1719619200,
        "authorMeta": {"name": "75hardofficial", "nickName": "75 HARD Official"},
        "stats": {"playCount": 1200000, "diggCount": 66000, "commentCount": 3100, "shareCount": 17000, "collectCount": 28000},
        "isVideo": True, "contentType": "reel",
        "webVideoUrl": "https://www.tiktok.com/@75hardofficial/video/7380004003",
    },
    {
        "id": "7380004004",
        "text": "Non-negotiables aren't optional. That's literally what the word means. Five things every day. No days off. No exceptions. No excuses.",
        "createTime": 1719532800,
        "authorMeta": {"name": "75hardofficial", "nickName": "75 HARD Official"},
        "stats": {"playCount": 870000, "diggCount": 47000, "commentCount": 2100, "shareCount": 11000, "collectCount": 19000},
        "isVideo": False, "contentType": "static",
        "webVideoUrl": "https://www.tiktok.com/@75hardofficial/video/7380004004",
    },
    {
        "id": "7380004005",
        "text": "The biggest lie in self-improvement: 'I'll start Monday.' Monday is now. It's always been now.",
        "createTime": 1719446400,
        "authorMeta": {"name": "75hardofficial", "nickName": "75 HARD Official"},
        "stats": {"playCount": 690000, "diggCount": 37000, "commentCount": 1700, "shareCount": 8500, "collectCount": 14000},
        "isVideo": True, "contentType": "reel",
        "webVideoUrl": "https://www.tiktok.com/@75hardofficial/video/7380004005",
    },

    # ── Men's Discipline ──────────────────────────────────────────────────────
    {
        "id": "7380005001",
        "text": "A man who can control himself can control anything. Start with 5 habits. Do them every day for 30 days. Watch your whole life change.",
        "createTime": 1719792000,
        "authorMeta": {"name": "mensdiscipline", "nickName": "Men's Discipline"},
        "stats": {"playCount": 2600000, "diggCount": 148000, "commentCount": 6700, "shareCount": 38000, "collectCount": 57000},
        "isVideo": True, "contentType": "reel",
        "webVideoUrl": "https://www.tiktok.com/@mensdiscipline/video/7380005001",
    },
    {
        "id": "7380005002",
        "text": "Score yourself every day. 0–10. Not to judge yourself. To see clearly. The men who track their habits are the men who build them.",
        "createTime": 1719705600,
        "authorMeta": {"name": "mensdiscipline", "nickName": "Men's Discipline"},
        "stats": {"playCount": 1800000, "diggCount": 99000, "commentCount": 4400, "shareCount": 24000, "collectCount": 40000},
        "isVideo": True, "contentType": "reel",
        "webVideoUrl": "https://www.tiktok.com/@mensdiscipline/video/7380005002",
    },
    {
        "id": "7380005003",
        "text": "Nobody is coming to save you. Not your boss, not your parents, not the algorithm. Show up for yourself or nobody will.",
        "createTime": 1719619200,
        "authorMeta": {"name": "mensdiscipline", "nickName": "Men's Discipline"},
        "stats": {"playCount": 1400000, "diggCount": 77000, "commentCount": 3500, "shareCount": 19000, "collectCount": 31000},
        "isVideo": True, "contentType": "reel",
        "webVideoUrl": "https://www.tiktok.com/@mensdiscipline/video/7380005003",
    },
    {
        "id": "7380005004",
        "text": "The accountability gap: knowing what to do vs. actually doing it every single day. That gap is where your goals go to die.",
        "createTime": 1719532800,
        "authorMeta": {"name": "mensdiscipline", "nickName": "Men's Discipline"},
        "stats": {"playCount": 1100000, "diggCount": 60000, "commentCount": 2800, "shareCount": 15000, "collectCount": 25000},
        "isVideo": False, "contentType": "static",
        "webVideoUrl": "https://www.tiktok.com/@mensdiscipline/video/7380005004",
    },
    {
        "id": "7380005005",
        "text": "30 days from now you'll wish you had started today. The best time to build a daily check-in habit was a year ago. Second best time is right now.",
        "createTime": 1719446400,
        "authorMeta": {"name": "mensdiscipline", "nickName": "Men's Discipline"},
        "stats": {"playCount": 780000, "diggCount": 42000, "commentCount": 1900, "shareCount": 10000, "collectCount": 17000},
        "isVideo": True, "contentType": "reel",
        "webVideoUrl": "https://www.tiktok.com/@mensdiscipline/video/7380005005",
    },
]


# ── Scraping layer ────────────────────────────────────────────────────────────

def scrape_competitor_data() -> List[Dict[str, Any]]:
    """
    Returns Apify-shaped post objects for all competitors.

    LIVE MODE: set APIFY_API_KEY env var and flip the call below to _fetch_apify().
    The downstream pipeline (analyze_with_claude, storage, email) is unchanged.
    """
    all_posts: List[Dict[str, Any]] = []
    for competitor in COMPETITORS:
        if APIFY_API_KEY:
            posts = _fetch_apify(competitor)
        else:
            posts = _fetch_mock(competitor)
        all_posts.extend(posts)
    print(f"[social_intel] Scraped {len(all_posts)} posts across {len(COMPETITORS)} accounts")
    return all_posts


def _fetch_mock(competitor: Dict) -> List[Dict[str, Any]]:
    handle = competitor["handle"]
    return [p for p in _MOCK_POSTS if p["authorMeta"]["name"] == handle]


def _fetch_apify(competitor: Dict) -> List[Dict[str, Any]]:
    """
    Real Apify call — actor: clockworks/free-tiktok-scraper
    Returns the same shape as _MOCK_POSTS entries.
    """
    handle = competitor["handle"]
    url = "https://api.apify.com/v2/acts/clockworks~free-tiktok-scraper/run-sync-get-dataset-items"
    try:
        r = httpx.post(
            url,
            params={"token": APIFY_API_KEY},
            json={
                "profiles": [f"https://www.tiktok.com/@{handle}"],
                "resultsPerPage": 10,
                "shouldDownloadVideos": False,
                "shouldDownloadCovers": False,
            },
            timeout=120,
        )
        if r.status_code == 200:
            return r.json()
        print(f"[social_intel] Apify error {r.status_code} for @{handle}: {r.text[:200]}")
    except Exception as exc:
        print(f"[social_intel] Apify request failed for @{handle}: {exc}")
    return []


# ── Intelligence engine (Claude) ──────────────────────────────────────────────

def _format_posts_for_claude(posts: List[Dict]) -> str:
    def fmt_num(n: int) -> str:
        if n >= 1_000_000:
            return f"{n/1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n/1_000:.0f}K"
        return str(n)

    lines = [f"COMPETITOR CONTENT ANALYSIS — {len(posts)} posts across {len(COMPETITORS)} accounts\n"]
    by_handle: Dict[str, List[Dict]] = {}
    for p in posts:
        h = p["authorMeta"]["name"]
        by_handle.setdefault(h, []).append(p)

    for handle, account_posts in by_handle.items():
        display = account_posts[0]["authorMeta"].get("nickName", handle)
        lines.append(f"\n=== @{handle} ({display}) ===")
        sorted_posts = sorted(account_posts, key=lambda x: x["stats"]["playCount"], reverse=True)
        for i, p in enumerate(sorted_posts, 1):
            s = p["stats"]
            ctype = p.get("contentType", "reel").upper()
            views  = fmt_num(s.get("playCount", 0))
            likes  = fmt_num(s.get("diggCount", 0))
            shares = fmt_num(s.get("shareCount", 0))
            first_line = p["text"].split(".")[0].strip()[:120]
            lines.append(
                f"{i}. [{ctype}, {views} views, {likes} likes, {shares} shares] "
                f'"{first_line}"'
            )
    return "\n".join(lines)


def analyze_with_claude(posts: List[Dict]) -> Dict[str, Any]:
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("[social_intel] ANTHROPIC_API_KEY not set — returning stub analysis")
        return _stub_analysis()

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        posts_text = _format_posts_for_claude(posts)

        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            messages=[{
                "role": "user",
                "content": (
                    "Here is this week's top performing content in the men's accountability "
                    "and discipline niche from TikTok and Instagram.\n\n"
                    f"{posts_text}\n\n"
                    "Analyze this content and return a JSON object with EXACTLY these keys:\n"
                    '- "top_hooks": array of 5 best opening lines (hooks) from the posts above '
                    "— copy them verbatim or improve them slightly\n"
                    '- "winning_themes": array of 3 objects, each with "theme" (string) and '
                    '"why" (1-sentence explanation of why it resonates)\n'
                    '- "ad_angles": array of 3 objects for CHKD app ads '
                    "(CHKD is a daily check-in app for men tracking 5 non-negotiables), "
                    'each with "headline" (max 8 words) and "first_line" (max 25 words)\n'
                    '- "content_ideas": array of 5 objects with "hook" (opening line, max 15 words) '
                    'and "structure" (3-4 bullet points describing the reel/video)\n'
                    '- "power_words": array of exactly 20 words or short phrases that real people '
                    "use in comments about discipline, accountability, and streaks — "
                    "raw, authentic language, not marketing speak\n\n"
                    "Return ONLY valid JSON. No markdown. No explanation. Just the JSON object."
                ),
            }],
        )

        raw = message.content[0].text.strip()
        # Strip code fences if Claude wraps the output
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1] if len(parts) > 1 else raw
            if raw.startswith("json"):
                raw = raw[4:].strip()

        return json.loads(raw)

    except Exception as exc:
        print(f"[social_intel] Claude analysis failed: {exc}")
        return _stub_analysis()


def _stub_analysis() -> Dict[str, Any]:
    """Returned when ANTHROPIC_API_KEY is missing. Still saves a complete record."""
    return {
        "top_hooks": [
            "Who's going to carry the boats?",
            "The most dangerous person on earth is the person who has learned to handle pain.",
            "Stop telling people your goals. Go dark. Go quiet. Let the work speak.",
            "A man who can control himself can control anything.",
            "Score yourself every day — not to judge yourself. To see clearly.",
        ],
        "winning_themes": [
            {
                "theme": "Discipline over motivation",
                "why": "Audiences are tired of motivation content — discipline-as-identity posts drive 2-3x more saves and shares.",
            },
            {
                "theme": "Daily tracking and streaks",
                "why": "Posts that show a daily check-in system (score, chain, non-negotiables) generate massive comment engagement from people sharing their own streak.",
            },
            {
                "theme": "Brutal self-honesty",
                "why": "Content that calls out comfortable lies ('I'll start Monday', 'I'm just not motivated') drives strong emotional reactions and shares.",
            },
        ],
        "ad_angles": [
            {
                "headline": "5 habits. Every day. No exceptions.",
                "first_line": "Stop relying on motivation. CHKD gives you a daily scorecard that holds you to your non-negotiables — no matter how you feel.",
            },
            {
                "headline": "What if you could see your discipline?",
                "first_line": "Most men know what they should do. CHKD is the daily check-in that makes sure you actually do it — and tracks your streak.",
            },
            {
                "headline": "Your 5 non-negotiables. Tracked daily.",
                "first_line": "The men winning right now aren't more motivated than you. They just have a system. CHKD is the system.",
            },
        ],
        "content_ideas": [
            {
                "hook": "I tracked my 5 non-negotiables every day for 30 days. Here's what happened.",
                "structure": [
                    "Show the CHKD check-in screen with a 30-day streak",
                    "Call out the 3 habits that moved the needle most",
                    "Show before/after: how you felt on Day 1 vs Day 30",
                    "CTA: 'Start your streak at getchkd.app'",
                ],
            },
            {
                "hook": "The accountability gap: why most men never change.",
                "structure": [
                    "Define the gap: knowing what to do vs. actually doing it",
                    "Show how most men track goals in their head (and fail)",
                    "Introduce the daily scorecard concept",
                    "CTA: 'Check your score every day — getchkd.app'",
                ],
            },
            {
                "hook": "Stop setting goals. Build these 5 systems instead.",
                "structure": [
                    "Call out why goals fail without daily systems",
                    "Walk through 5 non-negotiable habits framework",
                    "Show the CHKD scoring system (0-10 each habit)",
                    "CTA: 'Track your non-negotiables at getchkd.app'",
                ],
            },
            {
                "hook": "Day 14 is where most people quit. Not anymore.",
                "structure": [
                    "Explain the motivation cliff at day 14",
                    "Show how daily tracking bridges motivation → habit",
                    "Demonstrate the streak accountability feature",
                    "CTA: 'Don't let day 14 beat you — getchkd.app'",
                ],
            },
            {
                "hook": "Rate your day. Every day. Watch everything change.",
                "structure": [
                    "Introduce the concept: score your 5 non-negotiables each night",
                    "Show the CHKD interface scoring each habit",
                    "Explain how seeing the number makes you want to improve it",
                    "CTA: 'Download CHKD and score today'",
                ],
            },
        ],
        "power_words": [
            "locked in", "no excuses", "earned it", "the grind", "accountability",
            "streak", "non-negotiables", "dark mode", "earned not given", "discipline",
            "showing up", "the work", "own it", "standards", "no days off",
            "built different", "the gap", "consistent", "stack the days", "identity",
        ],
        "_stub": True,
    }


# ── Email summary ─────────────────────────────────────────────────────────────

def build_intel_email(analysis: Dict, week_of: date) -> tuple:
    week_str = week_of.strftime("%b %d, %Y")
    subject  = f"CHKD Social Intelligence — Week of {week_str}"

    hooks  = "\n".join(f"  • {h}" for h in analysis.get("top_hooks", []))
    themes = "\n".join(
        f"  • {t['theme']}: {t['why']}"
        for t in analysis.get("winning_themes", [])
    )
    angles = "\n".join(
        f"  • {a['headline']}\n    {a['first_line']}"
        for a in analysis.get("ad_angles", [])
    )
    ideas  = "\n".join(
        f"  • {c['hook']}"
        for c in analysis.get("content_ideas", [])
    )
    words  = "  " + ", ".join(analysis.get("power_words", []))

    stub_note = "\n[NOTE: Analysis used stub data — set ANTHROPIC_API_KEY for live Claude insights]\n" if analysis.get("_stub") else ""

    body = f"""CHKD Social Intelligence Report — Week of {week_str}
{stub_note}
TOP HOOKS THIS WEEK
{hooks}

WINNING THEMES
{themes}

AD ANGLES (ready to run)
{angles}

CONTENT IDEAS (ready to film)
{ideas}

POWER WORDS
{words}

View full report: https://duding.ai/dashboard/clients/
"""
    return subject, body


# ── Full pipeline ─────────────────────────────────────────────────────────────

def run_weekly_intelligence(client_id: int) -> Dict[str, Any]:
    """
    Orchestrates the full Social Intelligence pipeline.
    Called by job_social_intelligence() in outreach_engine.py.
    Returns the analysis dict (or {} on failure).
    """
    from db import SessionLocal
    from models.social_intelligence_report import SocialIntelligenceReport

    # Monday of current week
    today    = datetime.now(timezone.utc).date()
    week_of  = today - timedelta(days=today.weekday())

    print(f"[social_intel] Starting weekly intelligence — client_id={client_id}, week_of={week_of}")

    # 1. Scrape
    posts = scrape_competitor_data()
    if not posts:
        print("[social_intel] No posts returned — aborting")
        return {}

    # 2. Analyse
    analysis = analyze_with_claude(posts)

    # 3. Persist
    db = SessionLocal()
    try:
        # Upsert: replace if same client + week already exists
        existing = db.query(SocialIntelligenceReport).filter(
            SocialIntelligenceReport.client_id == client_id,
            SocialIntelligenceReport.week_of   == week_of,
        ).first()

        if existing:
            existing.raw_data = json.dumps(posts)
            existing.analysis = json.dumps(analysis)
        else:
            db.add(SocialIntelligenceReport(
                client_id=client_id,
                week_of=week_of,
                raw_data=json.dumps(posts),
                analysis=json.dumps(analysis),
            ))
        db.commit()
        print(f"[social_intel] Report saved for week_of={week_of}")
    except Exception as exc:
        db.rollback()
        print(f"[social_intel] ERROR saving report: {exc}")
    finally:
        db.close()

    # 4. Email Tommy
    try:
        subject, body = build_intel_email(analysis, week_of)
        send_email(TOMMY_EMAIL, subject, body, from_name="Duding")
        print(f"[social_intel] Report emailed to {TOMMY_EMAIL}")
    except Exception as exc:
        print(f"[social_intel] ERROR sending email: {exc}")

    return analysis
