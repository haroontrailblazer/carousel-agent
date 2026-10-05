"""Put a different hook on a finished cover, in seconds, with no model.

A reviewer who prefers another drafted hook, or writes their own, used to have
to reject the carousel and wait for a rework that re-ran the cover agent. The
cover is just the saved untitled picture (CoverSpec.base_artifact) with the
title composed on top, so a new title only needs that composition again: the
same framing, the same design, the new words.
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

from google.genai import types

from app.config import settings
from app.schemas import CarouselDesign, CoverSpec
from app.state import K_BUNDLE, K_COVER, K_DESIGN, K_PLAN
from app.tools import media_tools

PIPELINE_USER_ID = "pipeline"
_VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
MAX_TITLE_CHARS = 120


class RetitleUnavailable(RuntimeError):
    """The cover has no saved base picture (built before this existed)."""


def clean_hook(title: str, highlight: str) -> tuple[str, str]:
    """Normalise the reviewer's text; a highlight not in the title is dropped."""
    title = " ".join((title or "").split())
    highlight = " ".join((highlight or "").split())
    if not title:
        raise ValueError("Type a hook first.")
    if len(title) > MAX_TITLE_CHARS:
        raise ValueError(f"Keep the hook under {MAX_TITLE_CHARS} characters.")
    if highlight and highlight.upper() not in title.upper():
        highlight = ""
    # CoverSpec runs the published-text rules (no em dash, readable text).
    spec = CoverSpec(title=title, highlight=highlight)
    return spec.title, spec.highlight


async def retitle_cover(
    run_id: str, state: dict, title: str, highlight: str, artifacts: Any,
) -> dict:
    """Re-compose the cover with a new hook and save it as the latest version.

    Args:
        run_id: The run (ADK session id).
        state: The run's session state, as read by the review API.
        title, highlight: The reviewer's hook and its highlighted words.
        artifacts: The workspace-scoped artifact service.

    Returns:
        ``{"state": {key: new value, ...}, "warnings": [...], "cover": CoverSpec dict}``.
        The caller writes the state keys back to the session.

    Raises:
        RetitleUnavailable: no saved base picture for this cover.
        ValueError: the text breaks a published-text rule.
    """
    from app.agents.first_page_visual import hook_warnings

    title, highlight = clean_hook(title, highlight)
    cover = CoverSpec.model_validate(state.get(K_COVER) or {})
    if not cover.base_artifact:
        raise RetitleUnavailable(
            "This cover was built before quick retitling existed; reject it with the new hook instead."
        )
    design = CarouselDesign.model_validate(state.get(K_DESIGN) or {})
    base = await artifacts.load_artifact(
        app_name=settings.app_name, user_id=PIPELINE_USER_ID, session_id=run_id,
        filename=cover.base_artifact,
    )
    data = base.inline_data.data if base is not None and base.inline_data is not None else None
    if not data:
        raise RetitleUnavailable("The saved cover picture could not be loaded; reject it with the new hook instead.")

    suffix = Path(cover.base_artifact).suffix.lower()
    is_video = suffix in _VIDEO_SUFFIXES
    with tempfile.TemporaryDirectory(prefix="retitle-") as work:
        media = Path(work) / f"base{suffix}"
        media.write_bytes(data)
        result = await asyncio.to_thread(
            media_tools.compose_cover, str(media), title, highlight, is_video, work, design,
        )
        video_bytes = Path(result["video_path"]).read_bytes()
        poster_bytes = Path(result["poster_path"]).read_bytes()
    for name, payload, mime in (
        (cover.video_artifact or "cover.mp4", video_bytes, "video/mp4"),
        (cover.poster_artifact or "cover-poster.png", poster_bytes, "image/png"),
    ):
        await artifacts.save_artifact(
            app_name=settings.app_name, user_id=PIPELINE_USER_ID, session_id=run_id,
            filename=name, artifact=types.Part.from_bytes(data=payload, mime_type=mime),
        )

    new_cover = cover.model_copy(update={
        "title": title, "highlight": highlight,
        "duration_s": float(result.get("duration_s") or cover.duration_s),
    }).model_dump(mode="json")
    updates: dict = {K_COVER: new_cover}
    plan = dict(state.get(K_PLAN) or {})
    if plan:
        plan.update(hook_title=title, hook_highlight=highlight)
        updates[K_PLAN] = plan
    bundle = dict(state.get(K_BUNDLE) or {})
    if bundle.get("cover"):
        bundle["cover"] = {**bundle["cover"], "title": title, "highlight": highlight}
        updates[K_BUNDLE] = bundle
    return {"state": updates, "warnings": hook_warnings(title, highlight, design), "cover": new_cover}
