"""
services/discord.py — Discord integration for CHKD

Full server management + automated posting:
  - One-time server setup (categories, channels, pinned messages)
  - Streak / milestone announcements to #streaks
  - Daily check-in posts to #check-in (via APScheduler)
  - Weekly leaderboard posts to #leaderboard (via APScheduler)
  - New member detection via audit log polling
  - /coach slash command via Discord Interactions Endpoint

Required env vars:
  DISCORD_BOT_TOKEN        — bot token (Settings → Bot → Reset Token)
  DISCORD_SERVER_ID        — guild ID (right-click server → Copy Server ID)
  DISCORD_CHANNEL_ID       — invite-generation channel (existing #general)
  DISCORD_APPLICATION_ID   — app ID (Developer Portal → General Info)
  DISCORD_PUBLIC_KEY       — app public key (Developer Portal → General Info)

Bot permissions needed: MANAGE_CHANNELS, MANAGE_MESSAGES, VIEW_AUDIT_LOG,
                        SEND_MESSAGES, CREATE_INSTANT_INVITE
"""

import os
import time as _time
from typing import Any, Dict, List, Optional

import httpx

DISCORD_API = "https://discord.com/api/v10"

MILESTONE_MESSAGES: Dict[int, str] = {
    7:   "The streak is real.",
    14:  "Two weeks. No excuses.",
    30:  "A month of discipline. Unreal.",
    60:  "Two months. Top 1%.",
    100: "100 DAYS. UNBREAKABLE.",
}
MILESTONE_LENGTHS = sorted(MILESTONE_MESSAGES.keys())  # [7, 14, 30, 60, 100]

SERVER_STRUCTURE = [
    {
        "category": "📋 INFO",
        "channels": [
            {"name": "welcome",       "topic": "You earned your spot here."},
            {"name": "rules",         "topic": "The code we live by."},
            {"name": "announcements", "topic": "Official updates."},
        ],
    },
    {
        "category": "🔥 DAILY",
        "channels": [
            {"name": "check-in",  "topic": "Drop your score every day."},
            {"name": "wins",      "topic": "Post your wins here."},
            {"name": "streaks",   "topic": "Streak milestones get celebrated here."},
        ],
    },
    {
        "category": "⚔️ COMPETE",
        "channels": [
            {"name": "challenges",   "topic": "Active challenges and wagers."},
            {"name": "leaderboard",  "topic": "Weekly top 5."},
            {"name": "hall-of-fame", "topic": "30-day streak club."},
        ],
    },
    {
        "category": "💬 COMMUNITY",
        "channels": [
            {"name": "general",                 "topic": "General discussion."},
            {"name": "introductions",           "topic": "New? Introduce yourself."},
            {"name": "accountability-partners", "topic": "Find an accountability partner."},
        ],
    },
    {
        "category": "🤖 TOOLS",
        "channels": [
            {"name": "coach",        "topic": "Use /coach to get AI coaching."},
            {"name": "bot-commands", "topic": "Bot commands reference."},
        ],
    },
]

PINNED_MESSAGES: Dict[str, str] = {
    "welcome": (
        "Welcome to the CHKD Server.\n"
        "This is not a motivation group.\n"
        "This is where men who are already moving come to stay accountable.\n"
        "You earned your spot here with a 7-day streak.\n"
        "Don't waste it.\n"
        "— Tommy"
    ),
    "rules": (
        "THE CODE\n"
        "1. Show up every day or don't show up at all\n"
        "2. No excuses. No explanations. Just your score.\n"
        "3. Respect the streak. Yours and everyone else's.\n"
        "4. What's said in here stays in here.\n"
        "5. Build each other up. We don't tear down men who are trying.\n"
        "6. The app tracks it. The server holds you to it.\n"
        "CHKD ✓"
    ),
    "check-in": (
        "DAILY CHECK-IN\n"
        "Drop your score below.\n"
        "Format: Name / Score / Streak\n"
        "Example: Tommy / 5/5 / Day 14 🔥"
    ),
    "bot-commands": (
        "/coach — get personalized advice from the AI coach\n"
        "/streak — check your current streak\n"
        "/score — log today's score\n"
        "/leaderboard — see top 5 this week\n"
        "/challenge — start a wager challenge"
    ),
}

# Default channel names Discord creates on server creation (to delete after setup)
_DEFAULT_CHANNELS_TO_DELETE = {"general", "general-1"}


# ── Internal helpers ──────────────────────────────────────────────────────────

def _token() -> str:
    return os.getenv("DISCORD_BOT_TOKEN", "")


def _guild_id() -> str:
    return os.getenv("DISCORD_SERVER_ID", "")


def _channel_id() -> str:
    return os.getenv("DISCORD_CHANNEL_ID", "")


def _app_id() -> str:
    return os.getenv("DISCORD_APPLICATION_ID", "")


def _public_key() -> str:
    return os.getenv("DISCORD_PUBLIC_KEY", "")


def _headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bot {_token()}",
        "Content-Type": "application/json",
    }


# ── Channel cache ─────────────────────────────────────────────────────────────

_guild_channels_cache: Optional[List[Dict]] = None
_guild_channels_cache_ts: float = 0.0
_CHANNEL_CACHE_TTL = 300  # 5-minute TTL


def _invalidate_channel_cache() -> None:
    global _guild_channels_cache, _guild_channels_cache_ts
    _guild_channels_cache = None
    _guild_channels_cache_ts = 0.0


# ── Guild channel management ──────────────────────────────────────────────────

def get_guild_channels() -> List[Dict]:
    """Fetch all channels in the guild (cached for 5 minutes)."""
    global _guild_channels_cache, _guild_channels_cache_ts
    now = _time.monotonic()
    if _guild_channels_cache is not None and (now - _guild_channels_cache_ts) < _CHANNEL_CACHE_TTL:
        return _guild_channels_cache
    if not _token() or not _guild_id():
        return []
    try:
        r = httpx.get(
            f"{DISCORD_API}/guilds/{_guild_id()}/channels",
            headers=_headers(),
            timeout=10,
        )
        if r.status_code == 200:
            _guild_channels_cache = r.json() or []
            _guild_channels_cache_ts = now
            return _guild_channels_cache
        print(f"[discord] get_guild_channels {r.status_code}: {r.text[:200]}")
        return []
    except Exception as exc:
        print(f"[discord] get_guild_channels error: {exc}")
        return []


def get_channel_by_name(name: str) -> Optional[Dict]:
    """Find a text channel by name (exact, case-insensitive)."""
    target = name.lower()
    for ch in get_guild_channels():
        if ch.get("name", "").lower() == target and ch.get("type") in (0, 4):
            return ch
    return None


def _channel_id_for(name: str) -> Optional[str]:
    """Return channel ID by name, or None."""
    ch = get_channel_by_name(name)
    return ch["id"] if ch else None


def create_category(name: str) -> Optional[str]:
    """Create a GUILD_CATEGORY channel. Returns the new ID."""
    if not _token() or not _guild_id():
        return None
    try:
        r = httpx.post(
            f"{DISCORD_API}/guilds/{_guild_id()}/channels",
            headers=_headers(),
            json={"name": name, "type": 4},
            timeout=10,
        )
        if r.status_code == 201:
            _invalidate_channel_cache()
            return r.json()["id"]
        print(f"[discord] create_category failed {r.status_code}: {r.text[:200]}")
        return None
    except Exception as exc:
        print(f"[discord] create_category error: {exc}")
        return None


def create_channel(name: str, category_id: str, topic: str = "") -> Optional[str]:
    """Create a GUILD_TEXT channel under a category. Returns the new ID."""
    if not _token() or not _guild_id():
        return None
    body: Dict[str, Any] = {"name": name, "type": 0, "parent_id": category_id}
    if topic:
        body["topic"] = topic
    try:
        r = httpx.post(
            f"{DISCORD_API}/guilds/{_guild_id()}/channels",
            headers=_headers(),
            json=body,
            timeout=10,
        )
        if r.status_code == 201:
            _invalidate_channel_cache()
            return r.json()["id"]
        print(f"[discord] create_channel failed {r.status_code}: {r.text[:200]}")
        return None
    except Exception as exc:
        print(f"[discord] create_channel error: {exc}")
        return None


def delete_channel(channel_id: str) -> bool:
    if not _token():
        return False
    try:
        r = httpx.delete(
            f"{DISCORD_API}/channels/{channel_id}",
            headers=_headers(),
            timeout=10,
        )
        if r.status_code in (200, 204):
            _invalidate_channel_cache()
            return True
        print(f"[discord] delete_channel failed {r.status_code}: {r.text[:100]}")
        return False
    except Exception as exc:
        print(f"[discord] delete_channel error: {exc}")
        return False


# ── Message functions ─────────────────────────────────────────────────────────

def post_message(content: str, channel_id: Optional[str] = None) -> Optional[str]:
    """Post a message to a channel. Returns the message ID."""
    cid = channel_id or _channel_id()
    if not _token() or not cid:
        return None
    try:
        r = httpx.post(
            f"{DISCORD_API}/channels/{cid}/messages",
            headers=_headers(),
            json={"content": content},
            timeout=10,
        )
        if r.status_code == 200:
            return r.json().get("id")
        print(f"[discord] post_message failed {r.status_code}: {r.text[:200]}")
        return None
    except Exception as exc:
        print(f"[discord] post_message error: {exc}")
        return None


def post_to_channel_by_name(channel_name: str, content: str) -> Optional[str]:
    """Post to a channel identified by name. Returns message ID."""
    cid = _channel_id_for(channel_name)
    if not cid:
        print(f"[discord] channel '{channel_name}' not found — falling back to default")
        cid = _channel_id()
    return post_message(content, cid)


def pin_message(channel_id: str, message_id: str) -> bool:
    if not _token():
        return False
    try:
        r = httpx.put(
            f"{DISCORD_API}/channels/{channel_id}/pins/{message_id}",
            headers=_headers(),
            timeout=10,
        )
        return r.status_code == 204
    except Exception as exc:
        print(f"[discord] pin_message error: {exc}")
        return False


# ── Invite + streak ───────────────────────────────────────────────────────────

def create_invite(max_uses: int = 1, max_age: int = 604800) -> Optional[str]:
    """Generate a single-use invite. Returns the full URL."""
    if not _token() or not _channel_id():
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


def notify_streak(name: str, streak: int = 7) -> Optional[str]:
    """Post streak announcement to #streaks and return a single-use invite URL."""
    first = (name or "Someone").split()[0]
    invite_url = create_invite(max_uses=1, max_age=604800)

    cid = _channel_id_for("streaks") or _channel_id()
    lines = [
        f"🔥 **{first} just hit {streak} days straight.**",
        "",
        MILESTONE_MESSAGES.get(streak, "Keep going."),
        "",
    ]
    if invite_url:
        lines.append(f"They unlocked their invite: {invite_url}")
    else:
        lines.append("Welcome them when they show up.")

    post_message("\n".join(lines), cid)
    return invite_url


# ── Audit log polling (new member detection) ──────────────────────────────────

_last_audit_id: Optional[str] = None


def get_recent_joins(after_id: Optional[str] = None) -> List[Dict]:
    """
    Poll audit log for GUILD_MEMBER_ADD events (action_type=1).
    Returns newest-first list of member dicts.
    """
    if not _token() or not _guild_id():
        return []
    params: Dict[str, Any] = {"action_type": 1, "limit": 10}
    if after_id:
        params["after"] = after_id
    try:
        r = httpx.get(
            f"{DISCORD_API}/guilds/{_guild_id()}/audit-logs",
            headers=_headers(),
            params=params,
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            entries = data.get("audit_log_entries", [])
            users = {u["id"]: u for u in data.get("users", [])}
            result = []
            for entry in entries:
                uid = entry.get("target_id", "")
                user = users.get(uid, {})
                result.append({
                    "entry_id": entry.get("id", ""),
                    "user_id": uid,
                    "username": user.get("username", "Unknown"),
                    "display_name": user.get("global_name") or user.get("username", "Unknown"),
                })
            return result
        print(f"[discord] audit log {r.status_code}: {r.text[:200]}")
        return []
    except Exception as exc:
        print(f"[discord] get_recent_joins error: {exc}")
        return []


def init_audit_cursor() -> None:
    """Seed _last_audit_id so we don't retroactively welcome existing members."""
    global _last_audit_id
    joins = get_recent_joins()
    if joins:
        _last_audit_id = joins[0]["entry_id"]
    print(f"[discord] audit cursor initialized: {_last_audit_id}")


def check_new_members() -> int:
    """Check for new members since last poll. Post welcome for each. Returns count."""
    global _last_audit_id
    joins = get_recent_joins(after_id=_last_audit_id)
    if not joins:
        return 0

    welcome_cid = _channel_id_for("welcome") or _channel_id()
    intro_cid   = _channel_id_for("introductions")
    intro_ref   = f"<#{intro_cid}>" if intro_cid else "#introductions"

    welcomed = 0
    for join in reversed(joins):  # oldest first
        display = join.get("display_name") or join.get("username") or "Someone"
        msg = (
            f"🔥 **{display} just earned their spot.**\n"
            f"7-day streak. No days off.\n"
            f"Welcome to the server.\n"
            f"Introduce yourself in {intro_ref}."
        )
        post_message(msg, welcome_cid)
        welcomed += 1

    _last_audit_id = joins[0]["entry_id"]
    return welcomed


# ── Slash command registration + interaction verification ─────────────────────

def register_slash_commands() -> bool:
    """Register the /coach slash command globally via the Discord REST API."""
    app_id = _app_id()
    if not _token() or not app_id:
        print("[discord] DISCORD_APPLICATION_ID not set — skipping slash command registration")
        return False
    commands = [
        {
            "name": "coach",
            "description": "Get personalized advice from the CHKD AI coach",
            "options": [
                {
                    "name": "question",
                    "description": "What do you want help with?",
                    "type": 3,  # STRING
                    "required": True,
                }
            ],
        }
    ]
    try:
        r = httpx.put(
            f"{DISCORD_API}/applications/{app_id}/commands",
            headers=_headers(),
            json=commands,
            timeout=15,
        )
        if r.status_code in (200, 201):
            names = [c["name"] for c in (r.json() if isinstance(r.json(), list) else [])]
            print(f"[discord] Slash commands registered: {names}")
            return True
        print(f"[discord] Slash command registration failed {r.status_code}: {r.text[:200]}")
        return False
    except Exception as exc:
        print(f"[discord] register_slash_commands error: {exc}")
        return False


def verify_interaction(body: bytes, signature: str, timestamp: str) -> bool:
    """Verify Discord interaction request signature (Ed25519). Requires PyNaCl."""
    public_key = _public_key()
    if not public_key:
        return False
    try:
        from nacl.signing import VerifyKey
        from nacl.exceptions import BadSignatureError
        vk = VerifyKey(bytes.fromhex(public_key))
        vk.verify(timestamp.encode() + body, bytes.fromhex(signature))
        return True
    except Exception:
        return False


# ── Server setup (one-time) ───────────────────────────────────────────────────

def setup_server() -> Dict[str, Any]:
    """
    Build the full CHKD server structure:
    1. Create categories + channels
    2. Post and pin messages in the right channels
    3. Delete Discord default channels (general, General voice)
    4. Register /coach slash command
    5. Seed the audit cursor for new-member detection

    Returns a summary dict.
    """
    if not _token() or not _guild_id():
        return {"error": "DISCORD_BOT_TOKEN or DISCORD_SERVER_ID not set"}

    summary: Dict[str, Any] = {"created": [], "pinned": [], "deleted": [], "errors": []}

    # Index existing channels by name so we can skip re-creation
    existing_by_name = {ch["name"].lower(): ch for ch in get_guild_channels()}
    our_channel_names: set = set()

    for section in SERVER_STRUCTURE:
        cat_name = section["category"]

        # Reuse existing category or create it
        cat_key = cat_name.lower()
        if cat_key in existing_by_name and existing_by_name[cat_key].get("type") == 4:
            cat_id = existing_by_name[cat_key]["id"]
        else:
            cat_id = create_category(cat_name)
            _time.sleep(0.5)

        if not cat_id:
            summary["errors"].append(f"Failed to create category: {cat_name}")
            continue

        for ch_def in section["channels"]:
            ch_name = ch_def["name"]
            our_channel_names.add(ch_name.lower())

            # Reuse or create
            if ch_name.lower() in existing_by_name:
                ch_id = existing_by_name[ch_name.lower()]["id"]
                summary["created"].append(f"[exists] #{ch_name}")
            else:
                ch_id = create_channel(ch_name, cat_id, ch_def.get("topic", ""))
                _time.sleep(0.4)
                if ch_id:
                    summary["created"].append(f"#{ch_name}")
                else:
                    summary["errors"].append(f"Failed to create #{ch_name}")
                    continue

            # Post + pin message if defined
            msg_text = PINNED_MESSAGES.get(ch_name)
            if msg_text and ch_id:
                msg_id = post_message(msg_text, ch_id)
                _time.sleep(0.4)
                if msg_id:
                    ok = pin_message(ch_id, msg_id)
                    _time.sleep(0.4)
                    (summary["pinned"] if ok else summary["errors"]).append(f"#{ch_name}")

    # Delete Discord default channels that aren't in our structure
    _invalidate_channel_cache()
    for ch in get_guild_channels():
        name = ch.get("name", "").lower()
        if name in _DEFAULT_CHANNELS_TO_DELETE and name not in our_channel_names:
            ok = delete_channel(ch["id"])
            _time.sleep(0.3)
            (summary["deleted"] if ok else summary["errors"]).append(f"#{ch.get('name')}")

    # Also delete default voice channels (type=2, name "General")
    _invalidate_channel_cache()
    for ch in get_guild_channels():
        if ch.get("type") == 2 and ch.get("name", "").lower() == "general":
            ok = delete_channel(ch["id"])
            _time.sleep(0.3)
            (summary["deleted"] if ok else summary["errors"]).append(f"Voice #{ch.get('name')}")

    # Register /coach slash command
    register_slash_commands()

    # Seed audit cursor so we don't re-welcome existing members
    init_audit_cursor()

    return summary
