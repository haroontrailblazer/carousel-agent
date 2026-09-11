"""Multiple console-connected Telegram bots, each with an encrypted token.

The existing single-bot app_config value is read transparently and upgraded on
the next write. Changes lock the database row so concurrent connections cannot
overwrite each other. Sync senders use the cache refreshed at startup/on edits.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.services import db, secret_box
from app import tenancy

logger = logging.getLogger(__name__)
CONFIG_KEY = "telegram"
_cache: Optional[dict[str, dict]] = None
_workspaces = tenancy.ScopedDict()


def _current_cache():
    return _workspaces if tenancy.current() else (_cache or {})


def _store_cache(value):
    global _cache
    if tenancy.current():
        _workspaces.clear()
        _workspaces.update(value)
    else:
        _cache = value


def _stored_bots(stored: object) -> dict[str, dict]:
    if not isinstance(stored, dict):
        return {}
    if isinstance(stored.get("bots"), dict):
        return dict(stored["bots"])
    if stored.get("bot_token_enc"):
        token = secret_box.decrypt(str(stored["bot_token_enc"]))
        bot_id = str(stored.get("bot_id") or token.partition(":")[0] or "legacy")
        return {bot_id: dict(stored, bot_id=bot_id)}
    return {}


def _decode_all(stored: object) -> dict[str, dict]:
    return {
        bot_id: {
            "bot_id": bot_id,
            "bot_token": secret_box.decrypt(str(row.get("bot_token_enc") or "")),
            **{key: str(row.get(key) or "") for key in (
                "chat_id", "bot_username", "connected_by", "connected_at"
            )},
        }
        for bot_id, row in _stored_bots(stored).items()
    }


def all_credentials() -> list[dict]:
    """Snapshot of all destinations, including broken tokens for honest errors."""
    return [dict(bot) for bot in _current_cache().values()]


def credentials() -> dict:
    """Compatibility view for old callers; broadcasts use all_credentials."""
    return next(iter(all_credentials()), {key: "" for key in (
        "bot_id", "bot_token", "chat_id", "bot_username", "connected_by", "connected_at"
    )})


def configured() -> bool:
    return any(bot["bot_token"] and bot["chat_id"] for bot in all_credentials())


def source() -> str:
    return "console" if configured() else "unset"


async def load() -> dict:
    global _cache
    try:
        stored = await db.get_config(CONFIG_KEY, None)
    except Exception as exc:
        logger.warning("Could not load Telegram bots: %s", exc)
        return credentials()
    _store_cache(_decode_all(stored))
    return credentials()


async def save(
    *, bot_token: str, chat_id: str, bot_id: str = "",
    bot_username: str = "", connected_by: str = "", connected_at: str = "",
) -> dict:
    """Add a bot, or reconnect that same bot without replacing other bots."""
    global _cache
    bot_id = str(bot_id or bot_token.partition(":")[0])
    if not bot_token or not chat_id or not bot_id:
        raise ValueError("A verified bot and destination chat are required.")
    encrypted = secret_box.encrypt(bot_token)
    row = {
        "bot_id": bot_id, "bot_token_enc": encrypted,
        "chat_id": str(chat_id), "bot_username": bot_username,
        "connected_by": connected_by, "connected_at": connected_at,
    }

    def update(stored):
        bots = _stored_bots(stored)
        bots[bot_id] = row
        return {"bots": bots}

    stored = await db.update_config(CONFIG_KEY, update)
    _store_cache(_decode_all(stored))
    return dict(_current_cache()[bot_id])


async def clear(bot_id: str = "") -> None:
    """Remove just one bot, or all bots for the legacy disconnect endpoint."""
    global _cache

    def update(stored):
        bots = _stored_bots(stored)
        if bot_id:
            bots.pop(str(bot_id), None)
        else:
            bots.clear()
        return {"bots": bots}

    stored = await db.update_config(CONFIG_KEY, update)
    _store_cache(_decode_all(stored))
