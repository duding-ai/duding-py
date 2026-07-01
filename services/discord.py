"""
services/discord.py — Discord integration for CHKD

Handles:
  - Posting streak announcements to #general
  - Generating single-use server invite links (reward for 7-day streaks)

Required env vars:
  DISCORD_BOT_TOKEN   — bot token
  DISCORD_CHANNEL_ID  — channel to post in / generate invites for
  DISCORD_SERVER_ID   — guild ID (for invite verification)
"""

import os
from typing import Optional

import httpx

DISCORD_API = "https://discord.com/api/v10"


def _token() -> str:
    return os.getenv("DISCORD_BOT_TOKEN", "")


def _channel_id() -> str:
    return os.getenv("DISCORD_CHANNEL_ID", "")


def _headers() -> dict:
    return {
        "Authorization": f"Bot {_token()}",
        "Content-Type": "application/json",
    }


# ── Invite generation ─────────────────────────────────────────────────────────

def create_invite(max_uses: int = 1, max_age: int = 604800) -> Optional[str]:
    """
    Generate a single-use Discord invite to the CHKD server.
    max_age: seconds until expiry (default 7 days = 604800)
    Returns the full invite URL or None on failure.
    """
    if not _token() or not _channel_id():
        print("[discord] DISCORD_BOT_TOKEN or DISCORD_CHANNEL_ID not set")
        return None

    try:
        r = httpx.post(
            f"{DISCORD_API}/channels/{_channel_id()}/invites",
            headers=_headers(),
            json={"max_age": max_age, "max_uses": max_uses, "unique": True},
            timeout=10,
        )
        if r.status_code == 200:
            code = r.json().get("code")
            return f"https://discord.gg/{code}" if code else None
        print(f"[discord] create_invite failed {r.status_code}: {r.text[:200]}")
        return None
    except Exception as exc:
        print(f"[discord] create_invite error: {exc}")
        return None


# ── Channel messages ──────────────────────────────────────────────────────────

def post_message(content: str) -> bool:
    """Post a plain-text message to the configured channel."""
    if not _token() or not _channel_id():
        return False

    try:
        r = httpx.post(
            f"{DISCORD_API}/channels/{_channel_id()}/messages",
            headers=_headers(),
            json={"content": content},
            timeout=10,
        )
        if r.status_code == 200:
            return True
        print(f"[discord] post_message failed {r.status_code}: {r.text[:200]}")
        return False
    except Exception as exc:
        print(f"[discord] post_message error: {exc}")
        return False


def notify_streak(name: str, streak: int = 7) -> Optional[str]:
    """
    Post a streak announcement and generate an invite link.
    Returns the invite URL so it can be included in the email.
    """
    first = (name or "Someone").split()[0]

    invite_url = create_invite(max_uses=1, max_age=604800)

    lines = [
        f"🔥 **{first} just hit {streak} days straight.**",
        "",
        "That's discipline. Most men don't make it a week.",
        "",
    ]
    if invite_url:
        lines.append(f"They unlocked their invite: {invite_url}")
    else:
        lines.append("Welcome them when they show up.")

    post_message("\n".join(lines))
    return invite_url
