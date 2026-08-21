"""Gmail tools - review-request and publish-confirmation mails.

Used by the Review Dispatcher agent (``send_review_email``) and the Publisher
agent (``send_confirmation_email``). Auth is the Gmail API OAuth "installed
app" flow: client secrets at ``settings.gmail_credentials_path``, cached user
token at ``settings.gmail_token_path`` (refreshed automatically when expired).
The interactive browser flow only runs on attended local sessions (see
``GMAIL_ALLOW_INTERACTIVE_AUTH``); unattended runs with a missing/expired
token raise ``RuntimeError`` instead of blocking on a browser redirect.

Nothing in this module touches the network at import time - credentials and
the Gmail service are built lazily inside the send functions.
"""

from __future__ import annotations

import base64
import html
import logging
import mimetypes
import os
import sys
from email.message import EmailMessage
from io import BytesIO
from pathlib import Path
from typing import Any, Optional

import httplib2
from google.auth.transport.requests import Request as _GoogleAuthRequest
from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from app.config import settings

logger = logging.getLogger(__name__)

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
HTTP_TIMEOUT_S = 60
# Interactive browser OAuth is only for attended local runs (gated by
# GMAIL_ALLOW_INTERACTIVE_AUTH / a TTY) and always bounded: run_local_server
# raises WSGITimeoutError after this many seconds instead of waiting forever.
INTERACTIVE_AUTH_TIMEOUT_S = 300
# Inline previews are downscaled so the whole mail stays far below Gmail's
# 25 MB message cap even with 10 slides attached.
PREVIEW_MAX_WIDTH_PX = 600
PREVIEW_JPEG_QUALITY = 80

_BRAND_ORANGE = "#ff6a00"
_APPROVE_GREEN = "#1e8e3e"
_REJECT_RED = "#d93025"


# ---------------------------------------------------------------------------
# Auth / service plumbing
# ---------------------------------------------------------------------------
class _TimeoutRequest(_GoogleAuthRequest):
    """google-auth transport Request that always applies an explicit timeout."""

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("timeout", HTTP_TIMEOUT_S)
        return super().__call__(*args, **kwargs)


def _interactive_auth_allowed() -> bool:
    """True when the interactive browser OAuth flow may be started.

    Interactive auth is only for attended local runs: it is allowed when
    ``GMAIL_ALLOW_INTERACTIVE_AUTH`` is explicitly truthy ("1"/"true"/"yes"/
    "on"), forbidden when explicitly falsy ("0"/"false"/"no"/"off"), and
    otherwise falls back to whether stdin is a TTY (a human at a terminal).
    Headless runs (Cloud Run, schedulers) must fail loudly instead of blocking
    on a browser redirect that will never arrive.
    """
    flag = os.getenv("GMAIL_ALLOW_INTERACTIVE_AUTH", "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    if flag in ("0", "false", "no", "off"):
        return False
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError, OSError):
        return False


def _load_credentials() -> Credentials:
    """Load (or interactively create) Gmail send-only OAuth credentials.

    Order: cached token file -> refresh if expired -> full InstalledAppFlow
    (opens a browser; only needed on first run or after token revocation).
    The refreshed/new token is written back to ``settings.gmail_token_path``.

    The InstalledAppFlow step only runs when interactive auth is allowed (see
    :func:`_interactive_auth_allowed`) and is bounded by
    ``INTERACTIVE_AUTH_TIMEOUT_S``; unattended runs raise ``RuntimeError``
    immediately instead of hanging on a browser OAuth redirect.
    """
    token_path = Path(settings.gmail_token_path)
    credentials_path = Path(settings.gmail_credentials_path)

    creds: Optional[Credentials] = None
    if token_path.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_path), GMAIL_SCOPES)
        except (ValueError, OSError) as exc:  # corrupt/partial token file
            logger.warning("Ignoring unreadable Gmail token file %s: %s", token_path, exc)
            creds = None

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(_TimeoutRequest())
        except Exception as exc:  # refresh token revoked/expired -> re-auth
            logger.warning("Gmail token refresh failed (%s); starting OAuth flow.", exc)
            creds = None

    if not creds or not creds.valid:
        if not credentials_path.exists():
            raise FileNotFoundError(
                "Gmail OAuth client secrets not found at "
                f"{credentials_path}. Set GMAIL_CREDENTIALS_PATH in .env."
            )
        if not _interactive_auth_allowed():
            raise RuntimeError(
                "Gmail token missing/expired - re-run interactive auth locally "
                "(set GMAIL_ALLOW_INTERACTIVE_AUTH=1) to regenerate the token "
                f"file at {token_path} (settings.gmail_token_path). Interactive "
                "OAuth is disabled in unattended runs so the pipeline fails "
                "loudly instead of blocking on a browser redirect."
            )
        flow = InstalledAppFlow.from_client_secrets_file(
            str(credentials_path), GMAIL_SCOPES
        )
        # Bounded wait: raises WSGITimeoutError after the timeout instead of
        # blocking forever if nobody completes the browser flow.
        creds = flow.run_local_server(
            port=0, timeout_seconds=INTERACTIVE_AUTH_TIMEOUT_S
        )

    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds


def _gmail_service() -> Any:
    """Build a Gmail API service with an explicit per-request HTTP timeout."""
    creds = _load_credentials()
    authed_http = AuthorizedHttp(creds, http=httplib2.Http(timeout=HTTP_TIMEOUT_S))
    return build(
        "gmail",
        "v1",
        http=authed_http,
        cache_discovery=False,
        static_discovery=True,  # bundled discovery doc: no network fetch
    )


def _recipients() -> list[str]:
    """Reviewer addresses from settings; fail loudly if none configured."""
    if not settings.reviewer_emails:
        raise RuntimeError(
            "REVIEWER_EMAILS is empty - cannot send review/confirmation mail."
        )
    return settings.reviewer_emails


def _send(message: EmailMessage) -> dict:
    """base64url-encode and send a MIME message; return ``{"message_id": id}``."""
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    service = _gmail_service()
    sent = (
        service.users()
        .messages()
        .send(userId="me", body={"raw": raw})
        .execute()
    )
    return {"message_id": sent.get("id", "")}


# ---------------------------------------------------------------------------
# Inline-image helpers
# ---------------------------------------------------------------------------
def _preview_bytes(path: Path) -> tuple[bytes, str, str]:
    """Read an image, downscaled for mail. Returns (data, maintype, subtype).

    Uses Pillow to shrink to ``PREVIEW_MAX_WIDTH_PX`` JPEG thumbnails; falls
    back to the raw file bytes if Pillow can't open the file.
    """
    try:
        from PIL import Image

        with Image.open(path) as img:
            img = img.convert("RGB")
            if img.width > PREVIEW_MAX_WIDTH_PX:
                new_height = round(img.height * PREVIEW_MAX_WIDTH_PX / img.width)
                img = img.resize((PREVIEW_MAX_WIDTH_PX, new_height))
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=PREVIEW_JPEG_QUALITY)
            return buf.getvalue(), "image", "jpeg"
    except Exception as exc:
        logger.warning("Preview downscale failed for %s (%s); using raw bytes.", path, exc)
        mime, _ = mimetypes.guess_type(str(path))
        maintype, _, subtype = (mime or "image/png").partition("/")
        return path.read_bytes(), maintype, subtype


def _split_preview_paths(bundle: dict) -> tuple[Optional[Path], list[Path]]:
    """Extract (poster, slide images) from ``bundle["preview_paths"]``.

    Accepts either a plain list of local paths (first = poster/cover frame,
    rest = slide PNGs in order) or a mapping like
    ``{"poster": path, "slides": [paths...]}``. Missing files are skipped.
    """
    raw = bundle.get("preview_paths") or []
    poster: Optional[Path] = None
    slides: list[Path] = []

    if isinstance(raw, dict):
        poster_raw = raw.get("poster") or raw.get("cover")
        if poster_raw:
            poster = Path(str(poster_raw))
        slides = [Path(str(p)) for p in (raw.get("slides") or [])]
    else:
        paths = [Path(str(p)) for p in raw]
        if paths:
            poster, slides = paths[0], paths[1:]

    if poster is not None and not poster.exists():
        logger.warning("Poster preview missing on disk: %s", poster)
        poster = None
    existing_slides = []
    for p in slides:
        if p.exists():
            existing_slides.append(p)
        else:
            logger.warning("Slide preview missing on disk: %s", p)
    return poster, existing_slides


def _attach_inline(html_part: EmailMessage, cid: str, path: Path) -> None:
    """Attach one image to the HTML part as a CID-referenced inline resource."""
    data, maintype, subtype = _preview_bytes(path)
    html_part.add_related(
        data,
        maintype=maintype,
        subtype=subtype,
        cid=f"<{cid}>",
        filename=path.name,
    )


def _button_html(label: str, url: str, color: str) -> str:
    """One big, mail-client-safe link button (inline styles only)."""
    return (
        f'<a href="{html.escape(url, quote=True)}" '
        f'style="display:inline-block;padding:16px 40px;margin:8px;'
        f"background:{color};color:#ffffff;font-size:18px;font-weight:bold;"
        f'text-decoration:none;border-radius:8px;font-family:Arial,sans-serif;">'
        f"{html.escape(label)}</a>"
    )


# ---------------------------------------------------------------------------
# Public tool functions
# ---------------------------------------------------------------------------
def send_review_email(run_id: str, bundle: dict, round_no: int) -> dict:
    """Mail the reviewers a carousel preview with Approve/Reject links.

    Args:
        run_id: Pipeline run id - becomes part of the review URLs.
        bundle: The assembled ``Bundle`` as a dict. Must additionally carry
            ``preview_paths``: local file paths of the poster frame and the
            slide PNGs (list, poster first; or ``{"poster": ..., "slides":
            [...]}``). These are embedded inline via CID attachments.
        round_no: 1-based review round number, shown in subject and body.

    Returns:
        ``{"message_id": <gmail message id>}``.

    Raises:
        RuntimeError: if no reviewer emails are configured.
        FileNotFoundError: if OAuth client secrets are missing.
    """
    recipients = _recipients()
    cover = bundle.get("cover") or {}
    news_title = (
        bundle.get("news_title") or cover.get("title") or "Untitled carousel"
    )
    caption = bundle.get("caption") or ""
    poster_path, slide_paths = _split_preview_paths(bundle)

    base = settings.review_api_base_url.rstrip("/")
    approve_url = f"{base}/review/{run_id}/approve"
    reject_url = f"{base}/review/{run_id}/reject"

    safe_title = html.escape(str(news_title))

    poster_html = ""
    if poster_path is not None:
        poster_html = (
            '<p style="margin:16px 0;"><img src="cid:poster" alt="Cover poster" '
            'style="max-width:100%;width:480px;border-radius:8px;"></p>'
        )
    slide_imgs = "".join(
        f'<img src="cid:slide{i}" alt="Slide {i + 2}" '
        'style="width:180px;margin:4px;border-radius:6px;vertical-align:top;">'
        for i in range(len(slide_paths))
    )
    slides_html = (
        f'<div style="margin:8px 0;">{slide_imgs}</div>' if slide_imgs else ""
    )
    caption_html = (
        f'<p style="color:#555;font-size:14px;white-space:pre-wrap;">'
        f"<b>Caption:</b><br>{html.escape(caption)}</p>"
        if caption
        else ""
    )

    html_body = f"""
<div style="font-family:Arial,Helvetica,sans-serif;max-width:640px;margin:0 auto;color:#222;">
  <h2 style="margin-bottom:4px;">Carousel review needed
    <span style="color:{_BRAND_ORANGE};">- round {int(round_no)}</span></h2>
  <h3 style="margin-top:0;font-weight:normal;">{safe_title}</h3>
  {poster_html}
  {slides_html}
  {caption_html}
  <div style="text-align:center;margin:28px 0;">
    {_button_html("APPROVE", approve_url, _APPROVE_GREEN)}
    {_button_html("REJECT", reject_url, _REJECT_RED)}
  </div>
  <p style="font-size:14px;color:#444;background:#f6f6f6;padding:12px;border-radius:6px;">
    <b>Approve</b> - feedback is <i>optional</i> (anything you add still teaches the pipeline).<br>
    <b>Reject</b> - feedback is <b>required</b>: say exactly what is not good
    (first visual / texts / design / CTA / other) so the right agent can redo it.
  </p>
  <p style="font-size:12px;color:#999;">Run {html.escape(run_id)} · review round {int(round_no)}
    · Carousel Factory</p>
</div>
"""

    text_body = (
        f"Carousel review needed - round {int(round_no)}\n"
        f"Title: {news_title}\n\n"
        f"Approve (feedback optional): {approve_url}\n"
        f"Reject (feedback REQUIRED): {reject_url}\n\n"
        f"Run: {run_id}\n"
    )

    msg = EmailMessage()
    msg["From"] = settings.gmail_sender
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = f"[Review round {int(round_no)}] {news_title}"
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    html_part = msg.get_payload()[-1]  # the text/html alternative
    if poster_path is not None:
        _attach_inline(html_part, "poster", poster_path)
    for i, slide_path in enumerate(slide_paths):
        _attach_inline(html_part, f"slide{i}", slide_path)

    result = _send(msg)
    logger.info(
        "Review mail sent for run %s round %s (message %s)",
        run_id,
        round_no,
        result["message_id"],
    )
    return result


def send_confirmation_email(run_id: str, ig_permalink: str) -> dict:
    """Mail the reviewers that the carousel was published to Instagram.

    Args:
        run_id: Pipeline run id, for traceability in the mail footer.
        ig_permalink: Public Instagram permalink of the published carousel.

    Returns:
        ``{"message_id": <gmail message id>}``.
    """
    recipients = _recipients()
    safe_link = html.escape(ig_permalink, quote=True)

    html_body = f"""
<div style="font-family:Arial,Helvetica,sans-serif;max-width:640px;margin:0 auto;color:#222;">
  <h2 style="color:{_APPROVE_GREEN};">Published to Instagram ✔</h2>
  <p>The approved carousel is live:</p>
  <div style="text-align:center;margin:24px 0;">
    {_button_html("VIEW ON INSTAGRAM", ig_permalink, _BRAND_ORANGE)}
  </div>
  <p style="font-size:14px;"><a href="{safe_link}">{safe_link}</a></p>
  <p style="font-size:12px;color:#999;">Run {html.escape(run_id)} · Carousel Factory</p>
</div>
"""
    text_body = (
        "Published to Instagram.\n\n"
        f"Permalink: {ig_permalink}\n"
        f"Run: {run_id}\n"
    )

    msg = EmailMessage()
    msg["From"] = settings.gmail_sender
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = f"[Published] Carousel live - run {run_id}"
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    result = _send(msg)
    logger.info(
        "Confirmation mail sent for run %s (message %s)", run_id, result["message_id"]
    )
    return result
