"""Console settings: connecting the Telegram bot.

Deliberately NOT agent-driven. Connecting a bot is three fixed API calls with
three fixed outcomes; a model in that loop could only add latency, cost and
new ways to be wrong.

The token is a credential, so it is written but never read back: every
response carries a masked form, the bot's @username and whether it is
connected. Anyone who needs the real value already has it - they pasted it.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.services import telegram_config
from app.services.telegram_connect import (
    ConnectError,
    discover_chat,
    send_welcome,
    verify_token,
)
from web_api.auth import Identity
from web_api.deps import current_identity

logger = logging.getLogger(__name__)

router = APIRouter()


class TelegramConnectRequest(BaseModel):
    token: str = Field(min_length=1, max_length=200)


def _mask(token: str) -> str:
    """``8665967247:AAG...tNg`` - enough to recognise, not enough to use."""
    if not token:
        return ""
    head, _, tail = token.partition(":")
    if not tail:
        return f"{token[:4]}…{token[-3:]}" if len(token) > 10 else "…"
    return f"{head}:{tail[:3]}…{tail[-3:]}"


def _status() -> dict:
    creds = telegram_config.credentials()
    return {
        "connected": telegram_config.configured(),
        "source": telegram_config.source(),
        "bot_username": creds["bot_username"],
        "chat_id": creds["chat_id"],
        "token_masked": _mask(creds["bot_token"]),
        "connected_by": creds["connected_by"],
        "connected_at": creds["connected_at"],
    }


@router.get("/settings/telegram")
async def telegram_status(_identity: Identity = Depends(current_identity)) -> dict:
    """Whether a bot is connected, and which one."""
    return _status()


@router.post("/settings/telegram")
async def telegram_connect(
    payload: TelegramConnectRequest,
    identity: Identity = Depends(current_identity),
) -> dict:
    """Verify a bot token, find its chat, say hello, and store it.

    The chat id is discovered rather than asked for, because typing a numeric
    chat id is the step everyone gets wrong. Telegram will only reveal it once
    a human has messaged the bot, so a token that has never been messaged
    comes back as ``no_chat`` with instructions rather than an error - it is a
    step to complete, not a mistake.
    """
    token = payload.token.strip()

    # Every call is blocking httpx inside a request; keep the loop free.
    try:
        bot = await asyncio.to_thread(verify_token, token)
    except ConnectError as exc:
        raise HTTPException(400, {"code": exc.code, "message": exc.message}) from exc

    try:
        chat_id = await asyncio.to_thread(discover_chat, token)
    except ConnectError as exc:
        raise HTTPException(400, {"code": exc.code, "message": exc.message}) from exc

    if not chat_id:
        username = bot.get("username") or ""
        raise HTTPException(
            409,
            {
                "code": "no_chat",
                "message": (
                    "The bot is real, but it has never been messaged, so "
                    "Telegram will not say which chat to use. Open "
                    f"t.me/{username} and send it /start, then connect again."
                ),
                "bot_username": username,
            },
        )

    try:
        await asyncio.to_thread(send_welcome, token, chat_id)
    except ConnectError as exc:
        raise HTTPException(400, {"code": exc.code, "message": exc.message}) from exc

    await telegram_config.save(
        bot_token=token,
        chat_id=chat_id,
        bot_username=str(bot.get("username") or ""),
        connected_by=identity.email,
        connected_at=datetime.now(timezone.utc).isoformat(),
    )
    logger.info(
        "Telegram bot @%s connected to chat %s by %s.",
        bot.get("username"),
        chat_id,
        identity.email,
    )
    return {"result": "connected", **_status()}


@router.delete("/settings/telegram")
async def telegram_disconnect(
    identity: Identity = Depends(current_identity),
) -> dict:
    """Forget the stored bot. Any .env fallback takes over again."""
    await telegram_config.clear()
    logger.info("Telegram credentials cleared by %s.", identity.email)
    return {"result": "disconnected", **_status()}


__all__ = ["router"]
