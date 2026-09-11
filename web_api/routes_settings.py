"""Console settings: connecting the Telegram bot and Instagram accounts.

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
from io import BytesIO

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from PIL import Image
from pydantic import BaseModel, Field, field_validator

from app.config import settings
from app.services import (
    avatar_store,
    instagram_accounts,
    instagram_oauth,
    secret_box,
    telegram_config,
)
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
        "bots": [
            {key: value for key, value in bot.items() if key != "bot_token"}
            | {"token_masked": _mask(bot["bot_token"]),
               "connected": bool(bot["bot_token"] and bot["chat_id"])}
            for bot in telegram_config.all_credentials()
        ],
        "secrets_ready": secret_box.configured(),
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
    """All connected Telegram bots, with tokens masked."""
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

    try:
        await telegram_config.save(
            bot_token=token,
            bot_id=str(bot.get("id") or ""),
            chat_id=chat_id,
            bot_username=str(bot.get("username") or ""),
            connected_by=identity.email,
            connected_at=datetime.now(timezone.utc).isoformat(),
        )
    except secret_box.SecretsNotConfigured as exc:
        # Refuse rather than fall back to storing it in the clear: the whole
        # reason the token moved out of .env was to stop it living in plain
        # text somewhere.
        raise HTTPException(
            503, {"code": "secrets_unconfigured", "message": str(exc)}
        ) from exc
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
    """Disconnect all bots (legacy endpoint)."""
    await telegram_config.clear()
    logger.info("Telegram credentials cleared by %s.", identity.email)
    return {"result": "disconnected", **_status()}


@router.delete("/settings/telegram/{bot_id}")
async def telegram_disconnect_bot(
    bot_id: str, identity: Identity = Depends(current_identity),
) -> dict:
    """Disconnect one bot without changing any other destination."""
    await telegram_config.clear(bot_id)
    logger.info("Telegram bot %s disconnected by %s.", bot_id, identity.email)
    return {"result": "disconnected", **_status()}


@router.post("/profile/avatar")
async def upload_avatar(
    request: Request, identity: Identity = Depends(current_identity)
) -> dict:
    """Store the signed-in person's profile picture.

    The BROWSER compresses before sending - a phone camera photo is several
    megabytes and an avatar is displayed at 56px, so shipping the original
    would waste the upload, the storage and every page load afterwards. This
    end only enforces the ceiling.

    The returned URL is one of ours, not a storage URL: the media bucket is
    private, and a presigned link would expire long before a profile picture
    should.
    """
    payload = await request.body()
    try:
        await avatar_store.save(identity.email, payload)
    except ValueError as exc:
        raise HTTPException(400, {"code": "bad_image", "message": str(exc)}) from exc
    except Exception as exc:
        logger.exception("Storing the avatar for %s failed.", identity.email)
        raise HTTPException(
            502,
            {"code": "storage_error", "message": f"Could not store that image: {exc}"},
        ) from exc

    key = avatar_store.key_for(identity.email)
    digest = key.rsplit("/", 1)[-1].removesuffix(".webp")
    # The cache-buster is what makes a re-upload visible: the URL is otherwise
    # stable per person, so browsers would keep showing the previous face.
    return {"url": f"/api/profile/avatar/{digest}?v={len(payload)}"}


@router.get("/profile/avatar/{digest}")
async def get_avatar(
    digest: str, _identity: Identity = Depends(current_identity)
) -> Response:
    """Serve a stored avatar from the private bucket."""
    if not digest.isalnum() or len(digest) != 64:
        raise HTTPException(404, {"code": "not_found", "message": "No such avatar."})
    if avatar_store.key_for(_identity.email).rsplit("/", 1)[-1] != f"{digest}.webp":
        raise HTTPException(404, {"code": "not_found", "message": "No such avatar."})
    try:
        payload = await avatar_store.load(f"{avatar_store.PREFIX}/{digest}.webp")
    except Exception as exc:
        logger.warning("Reading avatar %s failed: %s", digest, exc)
        raise HTTPException(
            502, {"code": "storage_error", "message": "Could not read that image."}
        ) from exc
    if payload is None:
        raise HTTPException(404, {"code": "not_found", "message": "No such avatar."})
    return Response(
        content=payload,
        media_type=avatar_store.CONTENT_TYPE,
        # Private: these are behind the login and must not sit in a shared
        # proxy. Immutable within a version because the URL carries ?v=.
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.delete("/profile/avatar")
async def delete_avatar(identity: Identity = Depends(current_identity)) -> dict:
    """Remove the stored picture; the generated default takes over again."""
    await avatar_store.delete(identity.email)
    return {"result": "removed"}

# ---------------------------------------------------------------------------
# Instagram: connecting publishing accounts.
#
# Each account is identified from its own access token and stored encrypted.
# Instagram OAuth redirects are not a connection method in this console.
#
# Like the Telegram routes above, this is deliberately not agent-driven. It is
# a fixed sequence of HTTP calls with fixed outcomes.
# ---------------------------------------------------------------------------
class AccountRef(BaseModel):
    account_id: str = Field(min_length=1, max_length=64)


#: What Meta gives a long-lived Instagram token, and the only figure available
#: when a pasted one cannot be refreshed. A token carries no issue date and
#: Meta publishes no introspection endpoint, so this is an ASSUMPTION - see
#: ``_extend_pasted_token`` for what is done to avoid relying on it.
ASSUMED_LIFETIME_DAYS = 60


class InstagramTokenRequest(BaseModel):
    """A token pasted into the console, and optionally the account it is for."""

    token: str = Field(min_length=1, max_length=2000)
    #: Optional. Required only for a token minted through Facebook Login,
    #: which cannot say who it belongs to; for an Instagram Login token it is
    #: a guard against connecting the wrong account.
    ig_user_id: str = Field(default="", max_length=32)

    @field_validator("token", "ig_user_id")
    @classmethod
    def _trim(cls, value: str) -> str:
        """Tokens arrive by copy-paste, newline and stray spaces included."""
        return value.strip()

    @field_validator("ig_user_id")
    @classmethod
    def _numeric(cls, value: str) -> str:
        if value and not value.isdigit():
            raise ValueError(
                "An Instagram user id is all digits - it is not the @handle."
            )
        return value


def _redirect_uri() -> str:
    """The callback Meta must have allowlisted, absolute."""
    return f"{settings.public_base_url.rstrip('/')}/api/settings/instagram/callback"


def _instagram_status() -> dict:
    """Connected accounts plus whether connecting is possible at all."""
    return {
        "app_configured": bool(settings.ig_app_id and settings.ig_app_secret),
        "public_base_url_set": bool(settings.public_base_url),
        "secrets_ready": secret_box.configured(),
        "redirect_uri": _redirect_uri() if settings.public_base_url else "",
        "accounts": instagram_accounts.listing(),
    }


async def _store_avatar(ig_user_id: str, payload: bytes) -> str:
    """Put an account's profile picture in the bucket; return its key.

    Re-encoded to PNG rather than stored as fetched: the bytes become the
    favicon on every slide's brand rail, and normalising here means the render
    path never has to guess a format. Best effort - a picture that cannot be
    decoded leaves the account without one, and the rail draws a monogram.
    """
    try:
        with Image.open(BytesIO(payload)) as source:
            buffer = BytesIO()
            source.convert("RGBA").save(buffer, format="PNG")
        return await avatar_store.save_at(
            f"instagram/{ig_user_id}.png", buffer.getvalue(), "image/png"
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not store the profile picture: %s", exc)
        return ""


@router.get("/settings/instagram")
async def instagram_status(_identity: Identity = Depends(current_identity)) -> dict:
    """Which accounts are connected, and whether more can be."""
    return _instagram_status()


def _extend_pasted_token(token: str, auth_kind: str) -> tuple[str, int, str]:
    """Turn a pasted token into one whose expiry is known rather than assumed.

    A token typed into a form carries no issue date, and Meta publishes no
    introspection endpoint - so on its own, all that can be recorded is the 60
    days a long-lived token is granted. That guess is dangerous rather than
    merely imprecise: a token pasted on day 50 of its life would be filed as
    having 60 days left, and the nightly refresh job only looks at tokens
    expiring within a fortnight, so it would sit untouched until long after it
    had died.

    ``ig_refresh_token`` answers that question authoritatively AND resets the
    clock, so it is tried first. It refuses for a token less than 24 hours old,
    and does not exist at all for the Facebook Login path - neither is a reason
    to reject the connection, because the identity lookup has already proved
    the token works.

    Returns:
        ``(token, expires_in_seconds, "confirmed" | "assumed")``.
    """
    assumed = (token, ASSUMED_LIFETIME_DAYS * 86400, "assumed")
    if auth_kind != instagram_oauth.AUTH_KIND_INSTAGRAM:
        return assumed
    try:
        fresh, expires_in = instagram_oauth.refresh_long_lived(token)
    except instagram_oauth.OAuthError as exc:
        logger.info(
            "Pasted token was not refreshable (%s); assuming %d days.",
            exc.message,
            ASSUMED_LIFETIME_DAYS,
        )
        return assumed
    if not fresh or expires_in <= 0:
        # A refresh that reports no time left would file the account as dead on
        # arrival. Distrust it and keep the token that demonstrably works.
        logger.warning("Refresh returned expires_in=%s; keeping the pasted token.", expires_in)
        return assumed
    return fresh, expires_in, "confirmed"


@router.post("/settings/instagram/token")
async def instagram_connect_token(
    payload: InstagramTokenRequest,
    identity: Identity = Depends(current_identity),
) -> dict:
    """Connect an account from an access token somebody pasted.

    Each account is connected independently using its own access token.

    Nothing about STORAGE is relaxed: the token is Fernet-encrypted or refused,
    keyed on the Instagram user id so pasting a new token for an account
    already here replaces it, and never echoed back to the browser.
    """
    token = payload.token.strip()
    if not token:
        raise HTTPException(
            400, {"code": "bad_token", "message": "Paste an access token first."}
        )

    if not secret_box.configured():
        # Refuse before asking Meta anything: there is no point identifying a
        # token that could not be stored, and storing it in the clear is not on
        # offer.
        raise HTTPException(
            503,
            {
                "code": "secrets_unconfigured",
                "message": (
                    "SECRETS_KEY is not set, so this token cannot be stored "
                    "encrypted - and it will not be stored any other way."
                ),
            },
        )

    # Blocking httpx inside a request; keep the loop free.
    try:
        who, auth_kind = await asyncio.to_thread(
            instagram_oauth.identify,
            token,
            payload.ig_user_id,
            api_version=settings.ig_api_version,
        )
    except instagram_oauth.OAuthError as exc:
        logger.info("Pasted Instagram token refused: %s", exc.message)
        raise HTTPException(
            400, {"code": exc.code, "message": exc.message}
        ) from exc

    token, expires_in, expiry = await asyncio.to_thread(
        _extend_pasted_token, token, auth_kind
    )

    avatar_key = ""
    picture_url = who.get("profile_picture_url") or ""
    if picture_url:
        picture = await asyncio.to_thread(instagram_oauth.fetch_avatar, picture_url)
        if picture:
            avatar_key = await _store_avatar(who["ig_user_id"], picture)

    try:
        account = await instagram_accounts.save(
            ig_user_id=who["ig_user_id"],
            username=who["username"],
            name=who["name"],
            token=token,
            expires_in=expires_in,
            connected_by=identity.email,
            avatar_key=avatar_key,
            auth_kind=auth_kind,
        )
    except secret_box.SecretsNotConfigured as exc:
        raise HTTPException(
            503, {"code": "secrets_unconfigured", "message": str(exc)}
        ) from exc

    logger.info(
        "Instagram account @%s connected from a pasted %s token by %s (expiry %s).",
        account.username,
        auth_kind,
        identity.email,
        expiry,
    )
    return {
        "result": "connected",
        "account": account.public(),
        "expiry": expiry,
        **_instagram_status(),
    }


@router.get("/settings/instagram/{account_id}/avatar")
async def instagram_avatar(
    account_id: str, _identity: Identity = Depends(current_identity)
) -> Response:
    """Serve a connected account's profile picture from the private bucket.

    Served by us rather than linked from Meta's CDN: ``profile_picture_url``
    is a short-lived signed URL that would be dead by the time anyone loaded
    the profile page.
    """
    account = instagram_accounts.get(account_id)
    if account is None or not account.avatar_key:
        raise HTTPException(404, {"code": "not_found", "message": "No such picture."})
    try:
        payload = await avatar_store.load(account.avatar_key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Reading the picture for %s failed: %s", account_id, exc)
        raise HTTPException(
            502, {"code": "storage_error", "message": "Could not read that image."}
        ) from exc
    if payload is None:
        raise HTTPException(404, {"code": "not_found", "message": "No such picture."})
    return Response(
        content=payload,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.post("/settings/instagram/default")
async def instagram_set_default(
    payload: AccountRef, identity: Identity = Depends(current_identity)
) -> dict:
    """Choose which account new runs target unless told otherwise."""
    await instagram_accounts.set_default(payload.account_id)
    logger.info(
        "Instagram default set to %s by %s.", payload.account_id, identity.email
    )
    return {"result": "updated", **_instagram_status()}


@router.delete("/settings/instagram/{account_id}")
async def instagram_disconnect(
    account_id: str, identity: Identity = Depends(current_identity)
) -> dict:
    """Forget an account. Runs already made for it keep their account_id.

    Those runs cannot publish afterwards, and the publisher says exactly that
    rather than silently posting somewhere else.
    """
    await instagram_accounts.delete(account_id)
    logger.info("Instagram account %s disconnected by %s.", account_id, identity.email)
    return {"result": "disconnected", **_instagram_status()}


__all__ = ["router"]



@router.get("/settings/sources")
async def newsroom_sources(_identity: Identity = Depends(current_identity)):
    from app.services import source_config
    return await source_config.load()


@router.put("/settings/sources")
async def save_newsroom_sources(request: Request, _identity: Identity = Depends(current_identity)):
    from app.services import source_config
    try:
        return await source_config.save(await request.json())
    except ValueError as exc:
        raise HTTPException(422, {"message": str(exc)}) from None
    except (TypeError, AttributeError):
        raise HTTPException(422, {"message": "Enter HTTPS feed URLs or YouTube channel IDs, one per line (up to 30 each)."}) from None
