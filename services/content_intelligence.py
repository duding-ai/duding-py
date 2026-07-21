"""
services/content_intelligence.py — Get CHKD Content Intelligence

Tracks every TikTok/Instagram video CHKD posts: performance stats
(ingested from analytics screenshots via Claude vision, or entered
manually), computed engagement rates, and waitlist-signup attribution.

Required env vars:
  ANTHROPIC_API_KEY    — for screenshot stat extraction
  SUPABASE_URL, SUPABASE_SERVICE_KEY — for waitlist attribution pull

Phase 2 (not active): sync_platform_stats() pulls from the official
Meta Graph API / TikTok Display API once platform_credentials rows
exist. See README "Content Intelligence — Phase 2" section for what
each API can and cannot provide.
"""
from __future__ import annotations

import csv
import io
import json
import os
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from db import SessionLocal
from models.content_video import ContentVideo
from models.content_stats_snapshot import ContentStatsSnapshot
from models.waitlist_attribution import WaitlistAttribution
from models.platform_credentials import PlatformCredentials

CHKD_CLIENT_ID = 1

# ── Screenshot ingestion (Claude vision) ────────────────────────────────────

_SNAPSHOT_FIELDS = [
    "views", "accounts_reached", "total_viewers", "likes", "comments",
    "shares", "reposts", "saves", "profile_visits", "bio_link_taps", "follows",
    "avg_watch_time_seconds", "watched_full_pct", "retention_avg_pct",
    "skip_rate_pct", "drop_off_seconds", "total_play_time_seconds",
    "traffic_sources", "audience",
]

_EXTRACT_SYSTEM_PROMPT = (
    "You extract social media analytics from screenshots of TikTok Studio "
    "or Instagram Insights. Respond ONLY with a single JSON object — no "
    "prose, no markdown code fences. Use this exact schema (null for "
    "anything not visible in the images):\n\n"
    "{\n"
    '  "views": int|null, "accounts_reached": int|null, "total_viewers": int|null,\n'
    '  "likes": int|null, "comments": int|null, "shares": int|null, "reposts": int|null, "saves": int|null,\n'
    '  "profile_visits": int|null, "bio_link_taps": int|null, "follows": int|null,\n'
    '  "avg_watch_time_seconds": float|null, "watched_full_pct": float|null,\n'
    '  "retention_avg_pct": float|null, "skip_rate_pct": float|null,\n'
    '  "drop_off_seconds": float|null, "total_play_time_seconds": int|null,\n'
    '  "traffic_sources": {"<name>": float, ...}|null,\n'
    '  "audience": {"male_pct": float, "female_pct": float, "age": {"<range>": float}, '
    '"non_follower_pct": float, "new_viewer_pct": float, "returning_viewer_pct": float, '
    '"top_locations": {"<country>": float}}|null\n'
    "}\n\n"
    "Numbers only — strip '%' signs and commas. If multiple screenshots show "
    "different tabs of the SAME video, merge everything into one JSON object. "
    "Convert timestamps like '0:07' to seconds (7)."
)


def parse_stat_screenshots(images: List[Dict[str, Any]], platform: str) -> Dict[str, Any]:
    """
    images: list of {"media_type": "image/png"|"image/jpeg", "data": <base64 str>}
    Returns a dict matching _SNAPSHOT_FIELDS, or {"_error": "..."} on failure.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {"_error": "ANTHROPIC_API_KEY not set — use manual entry instead."}
    if not images:
        return {"_error": "No images uploaded."}

    try:
        # Same client setup as the /chkd/ai/coach proxy in app.py: import
        # inside the try, instantiate anthropic.Anthropic(api_key=...),
        # pull message.content[0].text with a fallback, catch broadly.
        import anthropic as _anthropic
        client = _anthropic.Anthropic(api_key=api_key)

        content_blocks = [
            {"type": "image", "source": {"type": "base64", "media_type": img["media_type"], "data": img["data"]}}
            for img in images
        ]
        content_blocks.append({
            "type": "text",
            "text": f"These screenshots are from {platform.title()} analytics for one video. Extract the metrics.",
        })

        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            system=_EXTRACT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content_blocks}],
        )
        raw = message.content[0].text if message.content else ""
        return _parse_json_defensively(raw)
    except Exception as exc:
        print(f"[content_intel] screenshot parse error: {exc}")
        return {"_error": f"AI extraction failed: {exc}"}


def _parse_json_defensively(raw: str) -> Dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        data = json.loads(text)
    except Exception as exc:
        print(f"[content_intel] JSON parse failed: {exc} — raw: {raw[:300]}")
        return {"_error": "Could not parse AI response — try manual entry."}

    return {k: data.get(k) for k in _SNAPSHOT_FIELDS if k in data} or {"_error": "AI response had no recognizable fields."}


# ── Snapshot save ────────────────────────────────────────────────────────────

def save_snapshot(db, video: ContentVideo, values: Dict[str, Any]) -> ContentStatsSnapshot:
    captured_at = datetime.now(timezone.utc)
    hours_since_post = None
    if video.posted_at:
        posted = video.posted_at if video.posted_at.tzinfo else video.posted_at.replace(tzinfo=timezone.utc)
        hours_since_post = round((captured_at - posted).total_seconds() / 3600, 1)

    snap = ContentStatsSnapshot(
        video_id=video.id,
        captured_at=captured_at,
        hours_since_post=hours_since_post,
        **{k: values.get(k) for k in _SNAPSHOT_FIELDS},
    )
    db.add(snap)
    db.commit()
    db.refresh(snap)
    return snap


# ── CSV backfill ─────────────────────────────────────────────────────────────

BACKFILL_CSV_COLUMNS = [
    "posted_date", "platform", "title", "length_seconds",
    "views", "likes", "comments", "shares", "saves",
]


def parse_backfill_csv(raw_bytes: bytes) -> List[Dict[str, Any]]:
    text = raw_bytes.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for row in reader:
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        if not row.get("posted_date") and not row.get("title"):
            continue
        rows.append(row)
    return rows


def backfill_csv_template() -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(BACKFILL_CSV_COLUMNS)
    writer.writerow(["2026-03-01", "tiktok", "Example hook text", "22", "1500", "80", "12", "9", "4"])
    return buf.getvalue()


def _to_int(v: str) -> Optional[int]:
    v = (v or "").strip().replace(",", "")
    if not v:
        return None
    try:
        return int(float(v))
    except ValueError:
        return None


def _to_float(v: str) -> Optional[float]:
    v = (v or "").strip().replace(",", "")
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def create_backfill_row(db, row: Dict[str, Any]) -> ContentVideo:
    posted_at = _parse_date_loose(row.get("posted_date", ""))
    video = ContentVideo(
        client_id=CHKD_CLIENT_ID,
        platform=(row.get("platform") or "tiktok").strip().lower(),
        title=row.get("title") or "Untitled backfill video",
        posted_at=posted_at,
        length_seconds=_to_float(row.get("length_seconds", "")),
        format_tags=["backfill"],
    )
    db.add(video)
    db.commit()
    db.refresh(video)

    save_snapshot(db, video, {
        "views": _to_int(row.get("views", "")),
        "likes": _to_int(row.get("likes", "")),
        "comments": _to_int(row.get("comments", "")),
        "shares": _to_int(row.get("shares", "")),
        "saves": _to_int(row.get("saves", "")),
    })
    return video


def _parse_date_loose(s: str) -> datetime:
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S", "%b %d, %Y"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return datetime.now(timezone.utc)


# ── Insights aggregation ─────────────────────────────────────────────────────

def get_insights(db) -> Dict[str, Any]:
    videos = (
        db.query(ContentVideo)
        .filter(ContentVideo.client_id == CHKD_CLIENT_ID)
        .order_by(ContentVideo.posted_at.asc())
        .all()
    )

    by_hook: Dict[str, Dict[str, List[float]]] = defaultdict(lambda: {"skip": [], "share": [], "views": []})
    by_cta: Dict[str, Dict[str, List[float]]] = defaultdict(lambda: {"skip": [], "share": [], "views": []})
    platform_pairs: Dict[Optional[int], Dict[str, ContentVideo]] = defaultdict(dict)
    ig_skip_trend: List[Dict[str, Any]] = []

    for v in videos:
        snap = v.latest_snapshot
        if not snap:
            continue

        hook = v.hook_style or "other"
        cta = v.cta_type or "none"
        if snap.skip_rate_pct is not None:
            by_hook[hook]["skip"].append(snap.skip_rate_pct)
            by_cta[cta]["skip"].append(snap.skip_rate_pct)
        if snap.share_rate is not None:
            by_hook[hook]["share"].append(snap.share_rate * 100)
            by_cta[cta]["share"].append(snap.share_rate * 100)
        if snap.views is not None:
            by_hook[hook]["views"].append(snap.views)
            by_cta[cta]["views"].append(snap.views)

        if v.series_number is not None:
            platform_pairs[v.series_number][v.platform] = v

        if v.platform == "instagram" and snap.skip_rate_pct is not None:
            ig_skip_trend.append({
                "series_number": v.series_number, "title": v.title,
                "posted_at": v.posted_at, "skip_rate_pct": snap.skip_rate_pct,
            })

    def _avg(lst):
        return round(sum(lst) / len(lst), 1) if lst else None

    hook_comparison = [
        {"hook_style": h, "avg_skip_rate_pct": _avg(d["skip"]), "avg_share_rate_pct": _avg(d["share"]),
         "avg_views": _avg(d["views"]), "n": len(d["views"])}
        for h, d in sorted(by_hook.items())
    ]
    cta_comparison = [
        {"cta_type": c, "avg_skip_rate_pct": _avg(d["skip"]), "avg_share_rate_pct": _avg(d["share"]),
         "avg_views": _avg(d["views"]), "n": len(d["views"])}
        for c, d in sorted(by_cta.items())
    ]
    platform_comparison = [
        {"series_number": num, "tiktok": pair.get("tiktok"), "instagram": pair.get("instagram")}
        for num, pair in sorted(platform_pairs.items(), key=lambda x: (x[0] is None, x[0]))
        if len(pair) >= 1
    ]
    ig_skip_trend.sort(key=lambda r: (r["series_number"] is None, r["series_number"]))

    # Funnel: views -> profile visits -> bio taps -> follows (summed across all videos with data)
    funnel = {"views": 0, "profile_visits": 0, "bio_link_taps": 0, "follows": 0}
    for v in videos:
        snap = v.latest_snapshot
        if not snap:
            continue
        funnel["views"] += snap.views or 0
        funnel["profile_visits"] += snap.profile_visits or 0
        funnel["bio_link_taps"] += snap.bio_link_taps or 0
        funnel["follows"] += snap.follows or 0

    waitlist_total = db.query(WaitlistAttribution).count()
    funnel["waitlist_signups"] = (
        sum(r.signups for r in db.query(WaitlistAttribution).all())
        if waitlist_total else None
    )

    return {
        "hook_comparison": hook_comparison,
        "cta_comparison": cta_comparison,
        "platform_comparison": platform_comparison,
        "ig_skip_trend": ig_skip_trend,
        "funnel": funnel,
    }


def get_signups_by_day(db, days: int = 60) -> List[Dict[str, Any]]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date()
    rows = (
        db.query(WaitlistAttribution)
        .filter(WaitlistAttribution.date >= since)
        .order_by(WaitlistAttribution.date.asc())
        .all()
    )
    by_day: Dict[date, int] = defaultdict(int)
    for r in rows:
        by_day[r.date] += r.signups
    return [{"date": d, "signups": n} for d, n in sorted(by_day.items())]


# ── Waitlist attribution nightly sync ────────────────────────────────────────

def sync_waitlist_attribution() -> int:
    """Pulls CHKD waitlist rows from Supabase, rolls up signups by
    (date, source), upserts into waitlist_attribution. Returns rows written."""
    import httpx

    sb_url = os.getenv("SUPABASE_URL", "https://vmpoexkcdcsbufqxwdwe.supabase.co")
    sb_key = os.getenv("SUPABASE_SERVICE_KEY", "")
    if not sb_key:
        print("[content_intel] SUPABASE_SERVICE_KEY not set — skipping waitlist attribution sync")
        return 0

    try:
        r = httpx.get(
            f"{sb_url}/rest/v1/waitlist",
            headers={"apikey": sb_key, "Authorization": f"Bearer {sb_key}"},
            params={"select": "created_at,source", "limit": "10000"},
            timeout=20,
        )
        if r.status_code != 200:
            print(f"[content_intel] waitlist fetch {r.status_code}: {r.text[:200]}")
            return 0
        rows = r.json() or []
    except Exception as exc:
        print(f"[content_intel] waitlist fetch error: {exc}")
        return 0

    counts: Dict[tuple, int] = defaultdict(int)
    for row in rows:
        created = (row.get("created_at") or "")[:10]
        if not created:
            continue
        try:
            d = datetime.strptime(created, "%Y-%m-%d").date()
        except ValueError:
            continue
        source = (row.get("source") or "direct").strip().lower() or "direct"
        counts[(d, source)] += 1

    db = SessionLocal()
    written = 0
    try:
        for (d, source), n in counts.items():
            existing = (
                db.query(WaitlistAttribution)
                .filter(WaitlistAttribution.date == d, WaitlistAttribution.source == source)
                .first()
            )
            if existing:
                if existing.signups != n:
                    existing.signups = n
                    written += 1
            else:
                db.add(WaitlistAttribution(date=d, source=source, signups=n))
                written += 1
        db.commit()
    except Exception as exc:
        db.rollback()
        print(f"[content_intel] waitlist attribution write error: {exc}")
    finally:
        db.close()

    print(f"[content_intel] waitlist attribution sync: {written} row(s) written/updated")
    return written


# ── Phase 2 — official API auto-pull (scaffolding only) ─────────────────────

def sync_platform_stats() -> int:
    """
    No-ops gracefully when no platform_credentials are connected. Once
    connected, pulls available metrics for videos with a known
    platform_video_id and writes a new snapshot per video.

    NOT ACTIVE — Instagram (Meta Graph API) and TikTok (Display API)
    both require a developer app review before they'll return real
    data; see README "Content Intelligence — Phase 2" for the exact
    application steps. Even once connected, these APIs only return
    basic counts (views/likes/comments/shares) — retention curves,
    skip rate, traffic sources, and audience breakdown are not exposed
    by either public API, so screenshot ingestion remains the source
    of truth for those fields indefinitely.
    """
    db = SessionLocal()
    try:
        creds = db.query(PlatformCredentials).filter(PlatformCredentials.status == "connected").all()
        if not creds:
            return 0

        synced = 0
        for cred in creds:
            videos = (
                db.query(ContentVideo)
                .filter(
                    ContentVideo.client_id == cred.client_id,
                    ContentVideo.platform == cred.platform,
                    ContentVideo.platform_video_id.isnot(None),
                )
                .all()
            )
            for video in videos:
                try:
                    stats = _fetch_platform_stats(cred, video)
                except Exception as exc:
                    print(f"[content_intel] platform sync error ({cred.platform} {video.id}): {exc}")
                    continue
                if stats:
                    save_snapshot(db, video, stats)
                    synced += 1
            cred.last_synced_at = datetime.now(timezone.utc)
        db.commit()
        return synced
    finally:
        db.close()


def _fetch_platform_stats(cred: PlatformCredentials, video: ContentVideo) -> Optional[Dict[str, Any]]:
    """Placeholder — wire up real Graph API / TikTok Display API calls
    here once app review is approved and access_token is live."""
    return None


# ── Seed data (5 known Get CHKD videos, for first-deploy dashboard) ────────

def seed_content_videos() -> int:
    """
    Idempotent per-row, not per-table: keyed on (client_id, platform,
    series_number), not "does content_videos have any rows at all".
    Safe to call on every restart — re-running after Tommy has added
    his own videos (which won't collide, since real videos won't
    coincidentally share series_number+platform with a seed row) only
    backfills whichever seed rows are still missing, it never
    skips-all-or-inserts-all based on table size.
    """
    db = SessionLocal()
    try:
        existing_keys = {
            (platform, series_number)
            for platform, series_number in db.query(ContentVideo.platform, ContentVideo.series_number)
            .filter(ContentVideo.client_id == CHKD_CLIENT_ID)
            .all()
        }

        def _dt(y, m, d, h=12, mi=0):
            return datetime(y, m, d, h, mi, tzinfo=timezone.utc)

        seed_rows = [
            # V1 — placeholder, partial data
            dict(
                video=dict(
                    client_id=CHKD_CLIENT_ID, platform="instagram", series_number=1,
                    title="V1 problem/intro", posted_at=_dt(2026, 6, 20),
                    hook_style="question", cta_type="none", format_tags=["intro"],
                    notes="partial data",
                ),
                snapshot=dict(
                    views=320, accounts_reached=211, skip_rate_pct=62.9,
                    avg_watch_time_seconds=11.0, profile_visits=13, bio_link_taps=7,
                ),
            ),
            # V2 — "who's watching" (cryptic) — TikTok
            dict(
                video=dict(
                    client_id=CHKD_CLIENT_ID, platform="tiktok", series_number=2,
                    title='V2 "who\'s watching 👀"', posted_at=_dt(2026, 6, 27),
                    hook_text="who's watching 👀", hook_style="cryptic",
                    cta_type="none", format_tags=["cryptic_hook"],
                ),
                snapshot=dict(
                    views=143, shares=29, avg_watch_time_seconds=6.8,
                    retention_avg_pct=35.0, watched_full_pct=8.44,
                ),
            ),
            # V2 — Instagram counterpart
            dict(
                video=dict(
                    client_id=CHKD_CLIENT_ID, platform="instagram", series_number=2,
                    title='V2 "who\'s watching 👀"', posted_at=_dt(2026, 6, 27),
                    hook_text="who's watching 👀", hook_style="cryptic",
                    cta_type="link_in_bio_caption_only", format_tags=["cryptic_hook"],
                ),
                snapshot=dict(
                    views=242, accounts_reached=152, avg_watch_time_seconds=17.0,
                    skip_rate_pct=58.5, profile_visits=8, bio_link_taps=1,
                ),
            ),
            # V3 — "let's be honest" (confessional) — TikTok
            dict(
                video=dict(
                    client_id=CHKD_CLIENT_ID, platform="tiktok", series_number=3,
                    title='V3 "let\'s be honest"', posted_at=_dt(2026, 7, 4),
                    hook_text="let's be honest", hook_style="confessional",
                    cta_type="none", format_tags=["confessional_hook"],
                ),
                snapshot=dict(
                    views=149, shares=23, avg_watch_time_seconds=10.0,
                    retention_avg_pct=27.0, drop_off_seconds=2.0,
                ),
            ),
            # V3 — Instagram counterpart
            dict(
                video=dict(
                    client_id=CHKD_CLIENT_ID, platform="instagram", series_number=3,
                    title='V3 "let\'s be honest"', posted_at=_dt(2026, 7, 4),
                    hook_text="let's be honest", hook_style="confessional",
                    cta_type="link_in_bio_caption_only", format_tags=["confessional_hook"],
                ),
                snapshot=dict(
                    views=287, accounts_reached=202, avg_watch_time_seconds=16.0,
                    skip_rate_pct=43.6, profile_visits=14, bio_link_taps=2,
                    reposts=1, saves=2,
                ),
            ),
            # V4 — "Netflix documentary" (trend) — TikTok
            dict(
                video=dict(
                    client_id=CHKD_CLIENT_ID, platform="tiktok", series_number=4,
                    title="V4 Netflix Doc trend", posted_at=_dt(2026, 7, 15, 22, 25),
                    hook_style="trend", cta_type="none", format_tags=["trend_format"],
                ),
                snapshot=dict(
                    views=198, shares=0, avg_watch_time_seconds=7.6,
                    retention_avg_pct=44.0, saves=3,
                    audience={"male_pct": 54},
                ),
            ),
            # V4 — Instagram counterpart
            dict(
                video=dict(
                    client_id=CHKD_CLIENT_ID, platform="instagram", series_number=4,
                    title="V4 Netflix Doc trend", posted_at=_dt(2026, 7, 15, 22, 25),
                    hook_style="trend", cta_type="link_in_bio_spoken", format_tags=["trend_format", "spoken_cta"],
                ),
                snapshot=dict(
                    views=750, accounts_reached=632, avg_watch_time_seconds=6.0,
                    skip_rate_pct=37.7, profile_visits=7, bio_link_taps=1,
                    likes=8, reposts=1, shares=1,
                    audience={"male_pct": 73.1, "non_follower_pct": 89.3},
                ),
            ),
        ]

        inserted = 0
        for row in seed_rows:
            key = (row["video"]["platform"], row["video"]["series_number"])
            if key in existing_keys:
                continue
            video = ContentVideo(**row["video"])
            db.add(video)
            db.commit()
            db.refresh(video)
            save_snapshot(db, video, row["snapshot"])
            existing_keys.add(key)
            inserted += 1

        if inserted:
            print(f"[content_intel] seeded {inserted} content_videos row(s)")
        return inserted
    except Exception as exc:
        db.rollback()
        print(f"[content_intel] seed error: {exc}")
        return 0
    finally:
        db.close()
