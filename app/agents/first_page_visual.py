"""First-Page Visual agent — builds the sourced cover video (slide 1).

The cover is a 4-8 second 1080x1350 video composed from media SOURCED from the
news update itself (never AI-generated): the announcement/event clip, or — as a
fallback — the update's best image turned into a 6 s slow-zoom video.  The
STRANGE-COVER overlay template plus the plan's hook title (white, with the
orange-gradient highlight phrase) are composited on top by
``app.tools.media_tools.compose_cover``.

The agent's tools write the final :class:`~app.schemas.CoverSpec` into session
state under ``K_COVER`` and save the rendered video + poster into the artifact
service — the agent itself has no ``output_schema`` (in ADK 2.7.0 tool-using
agents write state from inside tools via ``tool_context.state``).

Exposes :func:`build_first_page_visual_agent`.
"""

from __future__ import annotations

import asyncio
import subprocess
import uuid
from pathlib import Path
from typing import Any, Optional

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool, ToolContext
from google.genai import types

from app.config import agent_instructions, settings
from app.llm import resolve_model
from app.schemas import CarouselPlan, CoverSpec, NewsItem
from app.state import (
    AGENT_FIRST_PAGE_VISUAL,
    K_COVER,
    K_NEWS_ITEM,
    K_PLAN,
    K_RUN_ID,
    get_model,
    set_model,
)
from app.tools import media_tools

# Stable artifact filenames — the artifact service versions them per save, so
# rework rounds simply create a new version under the same name.
COVER_VIDEO_ARTIFACT = "cover.mp4"
COVER_POSTER_ARTIFACT = "cover-poster.png"

_RETRIM_FFMPEG_TIMEOUT_S = 300
_MIN_COVER_S = 4.0
_MAX_COVER_S = 8.0


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _run_workdir(tool_context: ToolContext) -> str:
    """Resolve (and create) the run-specific working folder for media files.

    Args:
        tool_context: The ADK tool context (session state holds ``K_RUN_ID``).

    Returns:
        The absolute folder path as a string (media_tools takes ``workdir: str``).
    """
    run_id = str(tool_context.state.get(K_RUN_ID) or "adhoc")
    wd = settings.workdir / run_id
    wd.mkdir(parents=True, exist_ok=True)
    return str(wd)


def _news_dict(tool_context: ToolContext) -> Optional[dict]:
    """Load the queued news item from session state as a plain dict."""
    news = get_model(tool_context.state, K_NEWS_ITEM, NewsItem)
    if news is None:
        return None
    return news.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Tools (each wraps app.tools.media_tools; blocking work runs in a thread)
# ---------------------------------------------------------------------------


async def find_source_clip(*, tool_context: ToolContext) -> dict:
    """Find the best SOURCED video (preferred) or image URL for the cover.

    Reads the news item from session state and scans its media_urls, its
    source_url (it may itself be a YouTube/Vimeo watch page) and media scraped
    off the source page. Video candidates are probed to confirm they play.

    Returns:
        Dict with keys: found (bool), url (str), is_video (bool),
        duration_s (float, 0.0 when unknown), origin
        ('media_urls' | 'source_url' | 'source_page' | ''), note (str).
        When no news item is queued, found is false and note explains why.
    """
    news = _news_dict(tool_context)
    if news is None:
        return {
            "found": False,
            "url": "",
            "is_video": False,
            "duration_s": 0.0,
            "origin": "",
            "note": "no news item in session state (K_NEWS_ITEM missing)",
        }
    return await asyncio.to_thread(media_tools.find_source_clip, news)


async def download_and_trim(
    url: str, max_s: int = 8, min_s: int = 4, *, tool_context: ToolContext
) -> dict:
    """Download a video URL and trim it to a silent 4-8 second H.264 mp4.

    Works with direct video files and any yt-dlp-supported page (YouTube,
    Vimeo, X, ...). Long videos are section-downloaded, so this is safe on
    full-length keynotes.

    Args:
        url: The video URL picked from find_source_clip (or the news item's
            media_urls directly).
        max_s: Maximum clip length in seconds (default 8, the Instagram-cover
            budget's upper bound).
        min_s: Minimum clip length in seconds (default 4).

    Returns:
        On success: ok (true), clip_path (local trimmed mp4),
        source_path (the untrimmed downloaded source file, '' if not kept —
        pass it to retrim_clip to cut a DIFFERENT moment), note.
        On failure: ok (false) and error (str) — try the next candidate.
    """
    workdir = _run_workdir(tool_context)
    try:
        clip_path = await asyncio.to_thread(
            media_tools.download_and_trim, url, max_s, min_s, workdir
        )
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "clip_path": "", "source_path": "", "error": str(exc)}

    # media_tools writes 'src-<stem>.<ext>' next to the returned
    # 'clip-<stem>.mp4'; recover it so retrim_clip can cut other moments.
    clip = Path(clip_path)
    source_path = ""
    stem = clip.stem
    if stem.startswith("clip-"):
        matches = sorted(clip.parent.glob(f"src-{stem[len('clip-'):]}.*"))
        if matches:
            source_path = str(matches[0])
    return {
        "ok": True,
        "clip_path": str(clip_path),
        "source_path": source_path,
        "error": "",
        "note": "trimmed clip ready; feed clip_path to build_cover",
    }


async def download_image(url: str, *, tool_context: ToolContext) -> dict:
    """Download the news item's best still image (the cover fallback source).

    Use this only when no playable sourced video exists; build_cover will turn
    the image into a 6 s slow-zoom cover video.

    Args:
        url: Direct image URL (from find_source_clip or the news media_urls).

    Returns:
        On success: ok (true) and path (local image file).
        On failure: ok (false) and error (str).
    """
    workdir = _run_workdir(tool_context)
    try:
        path = await asyncio.to_thread(media_tools.download_image, url, workdir)
    except (RuntimeError, OSError) as exc:
        return {"ok": False, "path": "", "error": str(exc)}
    return {"ok": True, "path": str(path), "error": ""}


async def retrim_clip(
    media_path: str,
    start_s: float,
    length_s: float = 6.0,
    *,
    tool_context: ToolContext,
) -> dict:
    """Cut a DIFFERENT 4-8 second moment out of an already-downloaded video.

    Use during rework when the reviewer wants another part of the clip: pass
    the source_path returned by download_and_trim (preferred — it holds the
    full downloaded footage) or any local video path, plus the wanted start
    offset in seconds.

    Args:
        media_path: Local path of the downloaded source video.
        start_s: Where the new clip should start, in seconds from the file's
            beginning (clamped into the valid range).
        length_s: Wanted clip length in seconds (clamped to 4-8).

    Returns:
        On success: ok (true), clip_path (new trimmed mp4), start_s and
        length_s actually used. On failure: ok (false) and error (str).
    """
    src = Path(media_path)
    if not src.exists():
        return {"ok": False, "clip_path": "", "error": f"file not found: {media_path}"}

    length = min(max(float(length_s), _MIN_COVER_S), _MAX_COVER_S)
    duration = await asyncio.to_thread(media_tools._media_duration, src)
    start = max(float(start_s), 0.0)
    if duration > 0:
        length = min(length, max(duration, _MIN_COVER_S))
        start = min(start, max(duration - length, 0.0))

    workdir = Path(_run_workdir(tool_context)) / "clips"
    workdir.mkdir(parents=True, exist_ok=True)
    out_path = workdir / f"retrim-{uuid.uuid4().hex[:12]}.mp4"
    cmd = [
        settings.ffmpeg_bin,
        "-y",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(src),
        "-t",
        f"{length:.3f}",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(out_path),
    ]

    def _run() -> None:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_RETRIM_FFMPEG_TIMEOUT_S
        )
        if proc.returncode != 0:
            tail = (proc.stderr or "")[-2000:]
            raise RuntimeError(f"ffmpeg failed (exit {proc.returncode}): {tail}")

    try:
        await asyncio.to_thread(_run)
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "clip_path": "", "error": str(exc)}
    return {
        "ok": True,
        "clip_path": str(out_path),
        "start_s": round(start, 3),
        "length_s": round(length, 3),
        "error": "",
    }


async def build_cover(
    media_path: str,
    is_video: bool,
    source_media_url: str = "",
    title: str = "",
    highlight: str = "",
    *,
    tool_context: ToolContext,
) -> dict:
    """Compose the final cover, save its artifacts, and write CoverSpec state.

    Scales/center-crops the media to 1080x1350, composites the STRANGE-COVER
    overlay template plus the hook title (white uppercase, orange-gradient
    highlight phrase), renders the mp4 + first-frame poster PNG, saves both to
    the artifact service, and stores the CoverSpec in session state. Call this
    exactly once at the end (again after rework to replace the cover).

    Args:
        media_path: Local path of the trimmed clip (from download_and_trim /
            retrim_clip) or the downloaded image (from download_image).
        is_video: True for a video clip, False for a still-image fallback
            (rendered as a 6 s slow-zoom video).
        source_media_url: The original URL the media came from (provenance,
            recorded in the CoverSpec).
        title: Optional title override; leave empty to use the plan's
            hook_title. Only override when rework feedback demands it.
        highlight: Optional highlight-phrase override; must be a verbatim
            substring of the title or it is dropped. Leave empty to use the
            plan's hook_highlight.

    Returns:
        On success: ok (true), video_artifact, poster_artifact, duration_s,
        title, highlight, used_fallback_image, artifact versions and warnings.
        On failure: ok (false) and error (str).
    """
    plan = get_model(tool_context.state, K_PLAN, CarouselPlan)
    warnings: list[str] = []

    final_title = title.strip() or (plan.hook_title.strip() if plan else "")
    if not final_title:
        return {
            "ok": False,
            "error": (
                "no title available: session state has no carousel plan and no "
                "title override was passed"
            ),
        }
    final_highlight = highlight.strip() or (plan.hook_highlight.strip() if plan else "")
    if final_highlight and final_highlight.upper() not in final_title.upper():
        warnings.append(
            f"highlight {final_highlight!r} is not a verbatim substring of the "
            "title; it was dropped (title rendered all-white)"
        )
        final_highlight = ""
    if len(final_title.split()) > 9:
        warnings.append("title exceeds the ~9 word hook budget (skills/cover-style.md)")

    workdir = _run_workdir(tool_context)
    try:
        result = await asyncio.to_thread(
            media_tools.compose_cover,
            media_path,
            final_title,
            final_highlight,
            is_video,
            workdir,
        )
    except (RuntimeError, FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": f"cover composition failed: {exc}"}

    video_path = Path(result["video_path"])
    poster_path = Path(result["poster_path"])
    duration_s = float(result.get("duration_s") or 0.0)
    if duration_s and not (_MIN_COVER_S - 0.5 <= duration_s <= _MAX_COVER_S + 0.5):
        warnings.append(f"cover duration {duration_s:.2f}s is outside the 4-8 s budget")

    try:
        video_bytes = await asyncio.to_thread(video_path.read_bytes)
        poster_bytes = await asyncio.to_thread(poster_path.read_bytes)
        video_version = await tool_context.save_artifact(
            COVER_VIDEO_ARTIFACT,
            types.Part.from_bytes(data=video_bytes, mime_type="video/mp4"),
        )
        poster_version = await tool_context.save_artifact(
            COVER_POSTER_ARTIFACT,
            types.Part.from_bytes(data=poster_bytes, mime_type="image/png"),
        )
    except (ValueError, OSError) as exc:
        # ValueError: artifact service not initialized on the runner.
        return {"ok": False, "error": f"could not save cover artifacts: {exc}"}

    spec = CoverSpec(
        video_artifact=COVER_VIDEO_ARTIFACT,
        poster_artifact=COVER_POSTER_ARTIFACT,
        source_media_url=source_media_url.strip(),
        title=final_title,
        highlight=final_highlight,
        duration_s=duration_s,
        used_fallback_image=not is_video,
    )
    set_model(tool_context.state, K_COVER, spec)

    return {
        "ok": True,
        "video_artifact": COVER_VIDEO_ARTIFACT,
        "poster_artifact": COVER_POSTER_ARTIFACT,
        "video_version": video_version,
        "poster_version": poster_version,
        "duration_s": duration_s,
        "title": final_title,
        "highlight": final_highlight,
        "used_fallback_image": not is_video,
        "warnings": warnings,
        "error": "",
    }


# ---------------------------------------------------------------------------
# Default instruction (mirrored in skills/agents/first_page_visual.md — the
# on-disk file wins at build time; this string is the fallback if it is gone).
# ---------------------------------------------------------------------------

DEFAULT_INSTRUCTION = """\
# First-Page Visual Agent

You build the COVER (slide 1) of an Instagram carousel: a 4-8 second,
1080x1350 (4:5) video SOURCED from the news update itself. You never touch any
other slide, never write body copy or captions, and never AI-generate media.

## Context (injected from session state)

- News item: {news_item?}
- Carousel plan: {carousel_plan?}
- Current cover (empty on the first pass): {cover?}
- REWORK FEEDBACK — when present this is the human reviewer's correction and
  OVERRIDES everything else: {rework_feedback?}
- Distilled feedback from past runs: {recent_feedback_notes?}

## Hard rules

1. The cover is NEVER AI-generated. It is sourced from the update: the
   announcement/event clip (trimmed to 4-8 s), or — fallback — the update's
   own image (paper screenshot, product UI, blog hero) turned into a 6 s
   slow-zoom cover video.
2. Cover ONLY. Do not create, modify, or discuss body slides or the CTA slide.
3. The title comes from the plan's hook_title and the orange phrase from
   hook_highlight. Only override them when rework feedback explicitly asks for
   a different title. The highlight must stay a VERBATIM substring of the
   title; keep the title to ~9 words or fewer.
4. You MUST finish by calling build_cover successfully — that is what saves
   the cover artifacts and records the CoverSpec for the rest of the pipeline.

## Workflow

1. Call find_source_clip to pick the best sourced media (video preferred).
2. If it returned a video: call download_and_trim with that URL to get a
   local 4-8 s clip. If the download fails, try the next plausible video URL
   from the news item's media_urls; if every video fails, use the image path.
3. If only an image was found (or all videos failed): call download_image with
   the best image URL.
4. Call build_cover with the local media path, is_video set accordingly, and
   source_media_url set to the original URL for provenance. Leave title and
   highlight empty so the plan's hook is used.
5. Finish with a one-paragraph summary: which media you used (URL and origin),
   sourced clip vs image fallback, final duration, and the artifact filenames.

## Failure handling

- Tools report failures as ok=false with an error message instead of crashing.
  Read the error, then try the next-best candidate (another video URL, then
  the best image).
- If there is truly no usable media at all, do NOT call build_cover with fake
  media. Say clearly that no sourced media could be found and why, so the
  human review can handle it.

## Rework

When rework feedback is present, treat it as your highest-priority
instruction and rebuild the cover accordingly:

- "different moment / wrong part of the clip" — call retrim_clip on the
  source_path kept from download_and_trim with a new start_s (or download a
  different candidate URL), then rebuild.
- "title / wording is off" — call build_cover with explicit title and
  highlight overrides (highlight must remain a verbatim substring).
- "bad image / wrong media" — pick the next-best media candidate (rerun
  find_source_clip or use another media_urls entry) and rebuild.

Always finish rework by calling build_cover again so the CoverSpec in state
and the cover artifacts are replaced with the corrected version.
"""


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def build_first_page_visual_agent() -> LlmAgent:
    """Build the configured First-Page Visual LlmAgent.

    The instruction is loaded from ``skills/agents/first_page_visual.md`` (the
    Learner agent may have amended it) with :data:`DEFAULT_INSTRUCTION` as the
    inline fallback. State is written by the tools (``tool_context.state``),
    so the agent needs no ``output_schema``/``output_key``.

    Returns:
        The ready-to-run ``LlmAgent`` named ``AGENT_FIRST_PAGE_VISUAL``.
    """
    instruction = agent_instructions(AGENT_FIRST_PAGE_VISUAL) or DEFAULT_INSTRUCTION
    return LlmAgent(
        name=AGENT_FIRST_PAGE_VISUAL,
        model=resolve_model(settings.utility_model),
        description=(
            "Builds the carousel's cover (slide 1): a 4-8 s 1080x1350 video "
            "sourced from the news update (never AI-generated), composited "
            "with the STRANGE-COVER template and the plan's hook title."
        ),
        instruction=instruction,
        tools=[
            FunctionTool(find_source_clip),
            FunctionTool(download_and_trim),
            FunctionTool(download_image),
            FunctionTool(retrim_clip),
            FunctionTool(build_cover),
        ],
        # Orchestrator-driven pipeline node: never LLM-transfer elsewhere.
        # (Both flags True + no sub_agents selects SingleFlow, so
        # transfer_to_agent is never offered to the model.)
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )
