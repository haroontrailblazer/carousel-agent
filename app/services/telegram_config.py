"""Runtime Telegram credentials, editable from the console.

The bot token used to live only in ``.env``, which meant connecting Telegram
was a file edit and a restart - not something the person actually reviewing
carousels could do. It now lives in ``app_config`` and is set from the profile
page.

The awkward part is that ``app.tools.telegram_tools`` is SYNCHRONOUS (the
dispatcher calls it through ``asyncio.to_thread``), so it cannot await a
database read to find out where to send. Hence a small process-level cache:
refreshed at startup and whenever the credentials change, read synchronously by
the tools.

Environment variables still work and still win nothing: the database value is
preferred when present, and ``.env`` is the fallback. That keeps existing
deployments running unchanged while making the console the normal way in.
"""

from __future__ import annotations

import logging
from typing import Optional

from app.config import settings
from app.services import db

logger = logging.getLogger(__name__)

#: app_config key holding ``{"bot_token": ..., "chat_id": ..., "bot_username": ...}``.
CONFIG_KEY = "telegram"

_cache: Optional[dict] = None


def credentials() -> dict:
    """Current bot token / chat id, database first, environment second.

    Synchronous on purpose - see the module docstring. Returns empty strings
    rather than None so callers can treat "unset" uniformly.
    """
    cached = _cache or {}
    return {
        "bot_token": str(cached.get("bot_token") or settings.telegram_bot_token or ""),
        "chat_id": str(cached.get("chat_id") or settings.telegram_chat_id or ""),
        "bot_username": str(cached.get("bot_username") or ""),
        "connected_by": str(cached.get("connected_by") or ""),
        "connected_at": str(cached.get("connected_at") or ""),
    }


def configured() -> bool:
    """Whether a review message can actually be sent right now."""
    creds = credentials()
    return bool(creds["bot_token"] and creds["chat_id"])


def source() -> str:
    """Where the live credentials came from - for the console to display."""
    if _cache and _cache.get("bot_token"):
        return "console"
    if settings.telegram_bot_token:
        return "environment"
    return "unset"


async def load() -> dict:
    """Refresh the cache from the database. Never raises."""
    global _cache
    try:
        stored = await db.get_config(CONFIG_KEY, None)
    except Exception as exc:  # a missing database must not break startup
        logger.warning("Could not load Telegram credentials: %s", exc)
        return credentials()
    _cache = dict(stored) if isinstance(stored, dict) else None
    return credentials()


async def save(
    *,
    bot_token: str,
    chat_id: str,
    bot_username: str = "",
    connected_by: str = "",
    connected_at: str = "",
) -> dict:
    """Persist credentials and update the cache in one step."""
    global _cache
    payload = {
        "bot_token": bot_token,
        "chat_id": str(chat_id),
        "bot_username": bot_username,
        "connected_by": connected_by,
        "connected_at": connected_at,
    }
    await db.set_config(CONFIG_KEY, payload)
    _cache = payload
    return credentials()


async def clear() -> None:
    """Forget console-configured credentials (env, if any, takes over again)."""
    global _cache
    await db.set_config(CONFIG_KEY, {})
    _cache = None


__all__ = [
    "CONFIG_KEY",
    "clear",
    "configured",
    "credentials",
    "load",
    "save",
    "source",
]
