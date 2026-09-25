"""First-Page Visual agent - builds the sourced cover video (slide 1).

The cover is a 4-8 second 1080x1350 video composed from media SOURCED from the
news update itself (never AI-generated): the announcement/event clip, or - as a
fallback - the update's best image turned into a 6 s slow-zoom video.  The
configured cover overlay - optional - plus the plan's hook title in the selected
design's exact text and highlight colors are composited on top by
``app.tools.media_tools.compose_cover``.

The agent's tools write the final :class:`~app.schemas.CoverSpec` into session
state under ``K_COVER`` and save the rendered video + poster into the artifact
service - the agent itself has no ``output_schema`` (in ADK 2.7.0 tool-using
agents write state from inside tools via ``tool_context.state``).

Exposes :func:`build_first_page_visual_agent`.
"""

from __future__ import annotations

import asyncio
import math
import os
import subprocess
import uuid
from pathlib import Path
from typing import Any, Optional

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool, ToolContext
from google.genai import types

from app.config import agent_instructions, settings
from app.design_limits import (
    HOOK_MAX_CHARS,
    HOOK_MAX_WORDS,
    hook_min_readable_size,
)
from app.llm import resolve_role_model
from app.schemas import CarouselDesign, CarouselPlan, CoverSpec, NewsItem, ResearchBrief
from app.state import (
    AGENT_FIRST_PAGE_VISUAL,
    K_COVER,
    K_DESIGN,
    K_NEWS_ITEM,
    K_PLAN,
    K_RESEARCH,
    K_RUN_ID,
    get_model,
    set_model,
)
from app.text_rules import require_no_em_dash
from app.tools import cover_vision, media_tools

# Stable artifact filenames - the artifact service versions them per save, so
# rework rounds simply create a new version under the same name.
COVER_VIDEO_ARTIFACT = "cover.mp4"
COVER_POSTER_ARTIFACT = "cover-poster.png"

_RETRIM_FFMPEG_TIMEOUT_S = 300
# Each inspection is one small billed vision call; this caps a run's spend
# even if the agent keeps rejecting candidates.
_MAX_INSPECTIONS = 6
_K_INSPECTIONS = "temp:cover_inspections"
# find_source_clip may hold a third-party video back behind up to five
# stills; it is the last resort once they are all rejected. One inspection
# past the cap is kept for a file downloaded from it, so the stills cannot
# use up the only look it would get (at most one more small vision call).
_K_HELD_VIDEOS = "temp:cover_held_videos"  # video_url values find_source_clip held back
_K_HELD_FILES = "temp:cover_held_video_files"  # media keys downloaded from them
_K_HELD_INSPECTED = "temp:cover_held_video_inspected"
# Verdicts by local media path, and which clip was cut from which file (and
# where it starts in it). Kept in session state (not temp:) so a rework round
# can rebuild the same crop.
_K_VERDICTS = "cover_media_verdicts"
_K_LINEAGE = "cover_media_lineage"
_MIN_COVER_S = float(settings.cover_clip_min_s)
_MAX_COVER_S = float(settings.cover_clip_max_s)
# A subject box describes one judged frame. A clip gets it only when it opens
# within this much of that frame (best_start_s is rounded for the agent).
_SAME_FRAME_TOLERANCE_S = 0.25
# End a retrim this far before a scene cut: the cut's own frame is the first
# frame of the NEXT shot, and a trim ending exactly on it can keep it.
_CUT_MARGIN_S = 0.05
_AI_ILLUSTRATION = "ai_illustration"


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


def _media_key(media_path: str | Path) -> str:
    """One spelling per file, so 'C:/x/a.mp4' and 'C:\\x\\a.mp4' meet in state.

    The agent re-types paths between tools; a verdict stored under one
    spelling must still be found through a lineage recorded under another.
    """
    return os.path.normcase(os.path.abspath(str(media_path)))


def _record_lineage(
    tool_context: ToolContext,
    derived: str | Path,
    parent: str | Path,
    start_s: Optional[float],
) -> None:
    """Remember that ``derived`` was cut from ``parent``, starting at ``start_s``.

    ``start_s`` is where the derived clip's first frame sits in the parent's
    timeline, or ``None`` when the trim chose it internally.
    """
    lineage = dict(tool_context.state.get(_K_LINEAGE) or {})
    lineage[_media_key(derived)] = {"parent": _media_key(parent), "start_s": start_s}
    tool_context.state[_K_LINEAGE] = lineage


def _lineage_chain(tool_context: ToolContext, media_path: str) -> list[tuple[str, Optional[float]]]:
    """This media and every file it was cut from, nearest first.

    Each entry pairs the file's key with where ``media_path``'s first frame
    sits in that file's timeline (``None`` once an unknown start is crossed).
    """
    lineage = tool_context.state.get(_K_LINEAGE) or {}
    chain: list[tuple[str, Optional[float]]] = []
    key: str = _media_key(media_path)
    offset: Optional[float] = 0.0
    seen: set[str] = set()
    while key and key not in seen:
        seen.add(key)
        chain.append((key, offset))
        link = lineage.get(key) or {}
        if isinstance(link, str):  # recorded before start offsets were kept
            link = {"parent": link, "start_s": None}
        start = link.get("start_s")
        offset = None if offset is None or start is None else offset + float(start)
        key = _media_key(link["parent"]) if link.get("parent") else ""
    return chain


def _verdict_for(
    tool_context: ToolContext, media_path: str
) -> tuple[Optional[dict], Optional[float]]:
    """The inspection of this media, or of the file it was trimmed from.

    Returns the stored verdict and where ``media_path``'s first frame sits in
    the inspected file's timeline (``None`` when unknown). Trims never crop,
    so the logo-free area judged on the source fits every clip cut from it;
    the subject box only fits a clip that opens on the judged frame, which
    the offset lets build_cover check.
    """
    verdicts = tool_context.state.get(_K_VERDICTS) or {}
    for key, offset in _lineage_chain(tool_context, media_path):
        if key in verdicts:
            return verdicts[key], offset
    return None, None


def _from_held_video(tool_context: ToolContext, media_path: str) -> bool:
    """True when this media, or a file it was cut from, is a held-back video."""
    held = set(tool_context.state.get(_K_HELD_FILES) or [])
    return any(key in held for key, _ in _lineage_chain(tool_context, media_path))


def _judged_ai_illustration(tool_context: ToolContext, media_path: str) -> bool:
    """True when this media, or any file it was cut from, was judged AI art."""
    verdicts = tool_context.state.get(_K_VERDICTS) or {}
    return any(
        _AI_ILLUSTRATION in ((verdicts.get(key) or {}).get("problems") or [])
        for key, _ in _lineage_chain(tool_context, media_path)
    )


_MIN_FALLBACK_SCORE = 3


def _weak_rejected(tool_context: ToolContext, media_path: str) -> Optional[int]:
    """The score of a rejected inspection below the fallback floor, else None.

    In a real run every candidate was rejected and the agent "used the
    highest score" - a text card scored 1 - so the cover was a blurred
    banner reading "Safety Bug Bo...". Below the floor the plain placeholder
    cover is the better fallback.
    """
    verdicts = tool_context.state.get(_K_VERDICTS) or {}
    for key, _ in _lineage_chain(tool_context, media_path):
        record = verdicts.get(key)
        if record:
            if record.get("verdict") == "reject" and int(record.get("score") or 0) < _MIN_FALLBACK_SCORE:
                return int(record.get("score") or 0)
            return None
    return None


def _news_dict(tool_context: ToolContext) -> Optional[dict]:
    """Load the queued news item from session state as a plain dict."""
    news = get_model(tool_context.state, K_NEWS_ITEM, NewsItem)
    if news is None:
        return None
    return news.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Tools (each wraps app.tools.media_tools; blocking work runs in a thread)
# ---------------------------------------------------------------------------


async def find_source_clip(
    search_query: str = "", *, tool_context: ToolContext
) -> dict:
    """Find the best SOURCED video (preferred) or image URL for the cover.

    Reads the news item from session state and scans its media_urls, its
    source_url (it may itself be a YouTube/Vimeo watch page), media scraped
    off the source page AND off every page linked inside the item's body
    text (og:image / og:video / <video> / iframes), plus the lead images of
    the articles the research brief cites (origin 'research_page'). When no
    sourced video plays, it web-searches for an event/announcement clip
    before falling back to the best image. Video candidates are probed to
    confirm they play.

    Args:
        search_query: Optional web-search phrase for the clip hunt (e.g.
            "<product> launch keynote official"); empty uses the news title.
            Pass a sharper query when re-calling after a miss or on rework.

    Returns:
        Dict with keys: found (bool), url (str), is_video (bool),
        duration_s (float, 0.0 when unknown), origin
        ('media_urls' | 'media_page' | 'source_url' | 'source_page' |
        'body_url' | 'body_page' | 'research_page' | 'trend_search' |
        'web_search' | ''), image_url + image_origin (the highest-ranked
        STILL-image candidate), image_candidates (up to five ranked, distinct
        pictures), image_first, video_url, video_duration_s, video_origin,
        trend_search (live-search status), and note (str).
        When the best playable video is someone else's (a web-search,
        trend-search or cited-article hit) and a story page supplied a photo,
        the photo is the pick: url is that image, is_video is false,
        image_first is true, and the held-back video is in video_url - only
        try it when every inspected image is rejected (one inspection is kept
        for it). Otherwise video_url is empty. Media the research brief
        listed counts as a cited article's, not as the story's own. Try
        ranked images in order when video downloads fail, BEFORE considering
        the placeholder.
        When nothing at all is found, found is false - build the cover from
        create_placeholder_background instead of giving up.
    """
    news = _news_dict(tool_context)
    if news is None:
        return {
            "found": False,
            "url": "",
            "is_video": False,
            "duration_s": 0.0,
            "origin": "",
            "image_url": "",
            "image_origin": "",
            "image_candidates": [],
            "image_first": False,
            "video_url": "",
            "video_duration_s": 0.0,
            "video_origin": "",
            "trend_search": "",
            "note": "no news item in session state (K_NEWS_ITEM missing)",
        }
    research = get_model(tool_context.state, K_RESEARCH, ResearchBrief)
    research_sources = list(research.sources) if research else []
    # save_research_brief also merges these into the news item's media_urls;
    # passing them separately keeps them from counting as the story's own.
    research_media = list(research.media_candidates) if research else []
    result = await asyncio.to_thread(
        media_tools.find_source_clip, news, search_query, research_sources, research_media
    )
    held_url = str(result.get("video_url") or "").strip()
    if held_url:
        held = list(tool_context.state.get(_K_HELD_VIDEOS) or [])
        if held_url not in held:
            tool_context.state[_K_HELD_VIDEOS] = held + [held_url]
    return result


async def download_and_trim(
    url: str, max_s: int = 0, min_s: int = 0, *, tool_context: ToolContext
) -> dict:
    """Download a video URL and trim it to a short silent H.264 cover clip.

    Works with direct video files and any yt-dlp-supported page (YouTube,
    Vimeo, X, ...). Long videos are section-downloaded, so this is safe on
    full-length keynotes.

    Args:
        url: The video URL picked from find_source_clip (or the news item's
            media_urls directly).
        max_s: Maximum clip length in seconds; 0 (the default) uses the
            configured cover window maximum.
        min_s: Minimum clip length in seconds; 0 (the default) uses the
            configured cover window minimum.

    Returns:
        On success: ok (true), clip_path (local trimmed mp4),
        source_path (the untrimmed downloaded source file, '' if not kept -
        pass it to retrim_clip to cut a DIFFERENT moment), note.
        On failure: ok (false) and error (str) - try the next candidate.
    """
    workdir = _run_workdir(tool_context)
    try:
        clip_path = await asyncio.to_thread(
            media_tools.download_and_trim,
            url,
            max_s or None,
            min_s or None,
            workdir,
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
            # media_tools picks the clip's start itself (0, or a little into
            # a long video), so its offset in the source is unknown.
            _record_lineage(tool_context, clip, source_path, None)
    if url.strip() in (tool_context.state.get(_K_HELD_VIDEOS) or []):
        # Downloaded from a held-back video_url: its inspection is reserved.
        held_files = list(tool_context.state.get(_K_HELD_FILES) or [])
        held_files += [_media_key(path) for path in (clip, source_path) if path]
        tool_context.state[_K_HELD_FILES] = held_files
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


async def create_placeholder_background(*, tool_context: ToolContext) -> dict:
    """Build the deterministic dark fallback background (LAST resort only).

    Use when find_source_clip found nothing at all and every download failed:
    a drawn (non-AI) dark gradient still the cover template + title composite
    onto, so the cover is ALWAYS created. Feed the returned path to
    build_cover with is_video=false and note the fallback in your summary.

    Returns:
        On success: ok (true) and path (local PNG).
        On failure: ok (false) and error (str).
    """
    workdir = _run_workdir(tool_context)
    try:
        path = await asyncio.to_thread(media_tools.placeholder_background, workdir)
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
    """Cut a short moment out of an already-downloaded video.

    Use it after inspect_cover_media approves a video (start_s=best_start_s,
    media_path=retrim_from) so the approved frame opens the cover and becomes
    its poster, and during rework when the reviewer wants another moment.

    The requested start is kept: when the footage after it is short, the
    clip gets shorter (down to the cover minimum) instead of starting
    earlier. A source shorter than the cover minimum is looped up to it, as
    download_and_trim does. The clip ends just before the next scene cut, so
    a good shot does not run on into a presenter or a title card; when that
    shot is shorter than the cover minimum, its last frame is held until the
    minimum is reached instead.

    Args:
        media_path: Local path of the downloaded source video.
        start_s: Where the new clip should start, in seconds from the file's
            beginning.
        length_s: Wanted clip length in seconds (clamped into the configured
            cover window).

    Returns:
        On success: ok (true), clip_path (new trimmed mp4), start_s and
        length_s actually used, start_moved (true only when not even the
        minimum length fit after the requested start), cut_s (seconds after
        start_s where the footage cuts to another shot, null when it does
        not), held_s (seconds the shot's last frame is frozen because the
        shot is shorter than the cover minimum; pick another moment if that
        looks wrong), looped (true when a short source was looped). On
        failure: ok (false) and error (str).
    """
    src = Path(media_path)
    if not src.exists():
        return {"ok": False, "clip_path": "", "error": f"file not found: {media_path}"}

    requested = max(float(start_s), 0.0)
    start = requested
    length = min(max(float(length_s), _MIN_COVER_S), _MAX_COVER_S)
    duration = await asyncio.to_thread(media_tools._media_duration, src)
    loops = 0
    window = length
    if duration >= _MIN_COVER_S:
        # Shorten before moving: moving the start changes the first frame,
        # which is the poster the inspection approved.
        start = min(start, duration - _MIN_COVER_S)
        length = window = min(length, duration - start)
    elif duration > 0:
        # Too short to hold the cover minimum: loop it, the way
        # download_and_trim does, still opening on the requested frame.
        start = start if start < duration - 0.1 else 0.0
        length, window = _MIN_COVER_S, duration - start
        loops = math.ceil((start + _MIN_COVER_S) / duration) - 1

    cut = await asyncio.to_thread(media_tools.next_scene_cut, src, start, window)
    hold = 0.0
    if cut is not None:
        shot = max(cut - _CUT_MARGIN_S, 0.1)
        if shot >= _MIN_COVER_S:
            length = min(length, shot)
        else:
            # The approved shot is shorter than a cover may be: freeze its
            # last frame up to the minimum rather than run into the next shot.
            length, hold, loops = shot, _MIN_COVER_S - shot, 0

    if loops:
        # Looped timestamps run on continuously, so trim can start mid-file.
        inputs = ["-stream_loop", str(loops), "-i", str(src)]
        video_filter = f"trim=start={start:.3f}:duration={length:.3f},setpts=PTS-STARTPTS"
    else:
        inputs = ["-ss", f"{start:.3f}", "-i", str(src)]
        video_filter = f"trim=duration={length:.3f},setpts=PTS-STARTPTS"
        if hold:
            video_filter += f",tpad=stop_mode=clone:stop_duration={hold:.3f}"

    workdir = Path(_run_workdir(tool_context)) / "clips"
    workdir.mkdir(parents=True, exist_ok=True)
    out_path = workdir / f"retrim-{uuid.uuid4().hex[:12]}.mp4"
    # No output -t: the trim filter sets the length, and an output -t would
    # cut off the held frames tpad adds.
    cmd = [
        settings.ffmpeg_bin,
        "-y",
        *inputs,
        "-vf",
        video_filter,
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
    _record_lineage(tool_context, out_path, src, start)
    return {
        "ok": True,
        "clip_path": str(out_path),
        "start_s": round(start, 3),
        "length_s": round(length + hold, 3),
        "start_moved": abs(start - requested) > 0.05,
        "cut_s": None if cut is None else round(cut, 3),
        "held_s": round(hold, 3),
        "looped": bool(loops),
        "error": "",
    }


def hook_warnings(
    title: str,
    highlight: str,
    design: CarouselDesign | None = None,
) -> list[str]:
    """Report everything wrong with a cover hook, without failing the run.

    Three separate things can make a first page not work, and only the first
    is visible in the text itself:

    1. the highlight is not really in the title, so nothing turns green;
    2. the hook runs past the word budget;
    3. the hook is short in words but wide on screen, so the renderer shrinks
       it. A seven-word hook can still fit at full size or land under 100 px
       depending purely on its letters, which is why the fitted size is
       measured here rather than inferred from the word count.

    These are warnings, never errors. A weak hook is worth telling the agent
    about; it is not worth losing the cover over.
    """
    clean = " ".join(str(title or "").split())
    if not clean:
        return []
    found: list[str] = []
    hl = " ".join(str(highlight or "").split())
    if hl and hl.upper() not in clean.upper():
        found.append(
            f"highlight {hl!r} is not a verbatim substring of the title; it "
            "was dropped (title rendered all-white)"
        )
    words = len(clean.split())
    if words > HOOK_MAX_WORDS:
        found.append(
            f"hook is {words} words; the budget is {HOOK_MAX_WORDS} words "
            "(skills/cover-style.md)"
        )
    else:
        fitted = media_tools.fitted_title_size(clean, design)
        floor = hook_min_readable_size(
            design.cover.title_size if design is not None else media_tools.COVER_TITLE_FONT_SIZE
        )
        if fitted < floor:
            found.append(
                f"hook renders at only {fitted}px, under the {floor}px "
                f"readable floor: {len(clean)} characters is too wide. Aim for "
                f"{HOOK_MAX_CHARS} characters or fewer (skills/cover-style.md)"
            )
    return found


async def inspect_cover_media(
    media_path: str, is_video: bool, *, tool_context: ToolContext
) -> dict:
    """LOOK at a downloaded candidate before using it as the cover.

    Lays out numbered frames (six across a video, or the still itself) and
    has a vision model judge them as a cover: does it show the story's real
    subject, or is it a document, slide, web page, screen recording, news
    presenter, another outlet's branded footage, logo or stock shot? Returns
    the best moment, where the subject sits, and the part of the frame that
    is free of other outlets' logos and captions. build_cover applies them by
    itself: the logo-free area to this media and any clip cut from it, the
    subject box to the image it judged (whatever the verdict) and, for a
    "use" verdict, to a clip that opens on the judged frame.

    For a video, pass the untrimmed source_path from download_and_trim when
    there is one (it holds more footage to choose from), otherwise clip_path.
    A video downloaded from find_source_clip's held-back video_url is still
    inspected after the regular inspections are used up (once).

    Args:
        media_path: Local video or image path.
        is_video: True for a video file.

    Returns:
        ok (bool), verdict ('use' | 'reject'), score (0-10), problems (list;
        'ai_illustration' means an AI-generated or drawn picture - never use
        it, build_cover refuses it), reason, best_start_s and retrim_from
        (video: call retrim_clip with media_path=retrim_from and
        start_s=best_start_s so the approved frame opens the cover and becomes
        its poster), focus_x / focus_y / focus_w / focus_h (subject box as 0-1
        fractions, all 0 when the renderer's normal crop already works),
        inspections_left. On failure ok is false and the cover should be built
        the old way from the best candidate.
    """
    # Checked before the budget is charged: a URL or a missing file costs no
    # vision call, and in a real run it used up a quarter of the budget.
    if "://" in str(media_path) or not Path(media_path).exists():
        return {
            "ok": False,
            "error": (
                f"not a downloaded file: {media_path}. Download it first "
                "(download_image, or download_and_trim for a video) and inspect "
                "the local path it returns. No inspection was used."
            ),
            "inspections_left": _MAX_INSPECTIONS - int(tool_context.state.get(_K_INSPECTIONS) or 0),
        }
    used = int(tool_context.state.get(_K_INSPECTIONS) or 0)
    reserved = (
        used >= _MAX_INSPECTIONS
        and not tool_context.state.get(_K_HELD_INSPECTED)
        and _from_held_video(tool_context, media_path)
    )
    if used >= _MAX_INSPECTIONS and not reserved:
        return {
            "ok": False,
            "error": "inspection budget used up; build the cover from the best-scoring candidate so far",
            "inspections_left": 0,
        }
    if reserved:
        tool_context.state[_K_HELD_INSPECTED] = True
    else:
        used += 1
        tool_context.state[_K_INSPECTIONS] = used
    left = _MAX_INSPECTIONS - used

    news = get_model(tool_context.state, K_NEWS_ITEM, NewsItem)
    plan = get_model(tool_context.state, K_PLAN, CarouselPlan)
    story = ""
    if news is not None:
        story = f"{news.title}. {news.summary}"
    hook = plan.hook_title if plan else ""
    workdir = _run_workdir(tool_context)
    try:
        sheet = await asyncio.to_thread(cover_vision.contact_sheet, media_path, is_video, workdir)
        verdict = await asyncio.to_thread(cover_vision.judge_cover_media, sheet, story, hook)
    except (RuntimeError, OSError) as exc:
        return {"ok": False, "error": str(exc), "inspections_left": left}

    focus = verdict["focus"] or {"x": 0.0, "y": 0.0, "w": 0.0, "h": 0.0}
    best_start = sheet.timestamps[verdict["best_frame"] - 1] if is_video else 0.0
    verdicts = dict(tool_context.state.get(_K_VERDICTS) or {})
    verdicts[_media_key(media_path)] = {
        "verdict": verdict["verdict"],
        "score": verdict["score"],
        "problems": list(verdict["problems"]),
        "focus": verdict["focus"],
        "clean": verdict["clean"],
        # The subject box belongs to this moment only (see build_cover).
        "best_start_s": round(best_start, 3),
    }
    tool_context.state[_K_VERDICTS] = verdicts
    return {
        "ok": True,
        "verdict": verdict["verdict"],
        "score": verdict["score"],
        "problems": verdict["problems"],
        "reason": verdict["reason"],
        "best_start_s": round(best_start, 2),
        "retrim_from": str(media_path) if is_video else "",
        "focus_x": round(focus["x"], 3),
        "focus_y": round(focus["y"], 3),
        "focus_w": round(focus["w"], 3),
        "focus_h": round(focus["h"], 3),
        "inspections_left": left,
        "error": "",
    }


async def build_cover(
    media_path: str,
    is_video: bool,
    source_media_url: str = "",
    title: str = "",
    highlight: str = "",
    focus_x: float = 0.0,
    focus_y: float = 0.0,
    focus_w: float = 0.0,
    focus_h: float = 0.0,
    use_inspection: bool = True,
    *,
    tool_context: ToolContext,
) -> dict:
    """Compose the final cover, save its artifacts, and write CoverSpec state.

    Smart-crops the media around its evaluated subject across the edge-to-edge
    full 4:5 cover, composites the configured cover overlay (if any) plus the
    hook title in the selected exact text and highlight colors, renders
    the mp4 + first-frame poster PNG, saves both to the artifact service, and
    stores the CoverSpec in session state. Call this exactly once at the end
    (again after rework to replace the cover). Media an inspection flagged as
    ai_illustration (or a clip cut from it) is refused: the cover is never
    AI-generated.

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
        focus_x, focus_y, focus_w, focus_h: Optional subject box override
            (0-1 fractions of the frame). Leave all 0: an inspection's
            subject box is applied automatically to the image it judged,
            whatever its verdict (a still is the judged frame), and an
            approved ("use") one to a clip that opens on the judged frame
            (retrim_clip at best_start_s) - the cover zooms to the subject,
            keeps it above the title, and lays a wide group shot across the
            top instead of cropping it to one face. A clip showing another
            moment keeps the renderer's normal crop. The logo-free area of
            any inspection of the file a clip was cut from carries over to
            every clip, so other outlets' logos stay out. Pass a box only to
            correct the crop, e.g. one around every person a reviewer says
            was cut off.
        use_inspection: Set false to ignore the stored inspection's crop
            (subject box and logo-free area) and use the renderer's normal
            crop. That crop keeps one face of a group shot, so it is not the
            answer to a subject being cut off (pass a focus box instead).

    Returns:
        On success: ok (true), video_artifact, poster_artifact, duration_s,
        title, highlight, used_fallback_image, artifact versions and warnings.
        On failure: ok (false) and error (str).
    """
    weak = _weak_rejected(tool_context, media_path)
    if weak is not None:
        return {
            "ok": False,
            "error": (
                f"the inspection rejected this media with a score of {weak} "
                "(a text card, document or unrelated graphic makes a worse "
                "cover than a plain one) - build from a candidate that scored "
                "3 or more, or from create_placeholder_background"
            ),
        }
    if _judged_ai_illustration(tool_context, media_path):
        # Checked whatever use_inspection says: this is the hard no-AI rule,
        # not a crop preference.
        return {
            "ok": False,
            "error": (
                "the inspection judged this media an AI-generated or drawn "
                "illustration (ai_illustration); the cover is never "
                "AI-generated - build from the next candidate, or from "
                "create_placeholder_background when none is left"
            ),
        }
    plan = get_model(tool_context.state, K_PLAN, CarouselPlan)
    design = get_model(tool_context.state, K_DESIGN, CarouselDesign) or CarouselDesign()
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
    try:
        require_no_em_dash([final_title, final_highlight], "cover copy")
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    warnings.extend(hook_warnings(final_title, final_highlight, design))
    if final_highlight and final_highlight.upper() not in final_title.upper():
        final_highlight = ""

    workdir = _run_workdir(tool_context)
    stored, offset = _verdict_for(tool_context, media_path) if use_inspection else (None, None)
    stored = stored or {}
    # The subject box was judged on one frame. A still is that frame; a clip
    # only shows it when it opens on it - any other moment may have moved on
    # to another shot, and zooming there crops empty background.
    opens_on_judged_frame = not is_video or (
        offset is not None
        and abs(offset - float(stored.get("best_start_s") or 0.0)) <= _SAME_FRAME_TOLERANCE_S
    )
    focus: Optional[dict] = None
    if focus_w > 0 and focus_h > 0:
        focus = {"x": focus_x, "y": focus_y, "w": focus_w, "h": focus_h}
    elif stored.get("focus") and (not is_video or stored.get("verdict") == "use"):
        # A still is the judged frame, so its subject box fits whatever the
        # verdict: when every candidate is rejected the agent builds from the
        # best-scoring one, and a group photo rejected for a corner bug would
        # otherwise get the renderer's crop, one face filling the cover.
        if opens_on_judged_frame:
            focus = stored["focus"]
        else:
            warnings.append(
                "the inspection's subject crop was not applied: this clip does not "
                "open on the inspected frame (retrim at best_start_s, or inspect "
                "this clip, to get a crop for it)"
            )
    # Logos and captions stay put, so the logo-free area fits every clip.
    clean = stored.get("clean")
    if focus or clean:
        try:
            media_path = await asyncio.to_thread(
                cover_vision.apply_focus, media_path, is_video, focus, workdir, clean
            )
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
            warnings.append(f"focus crop skipped, used the normal crop: {exc}")
    try:
        result = await asyncio.to_thread(
            media_tools.compose_cover,
            media_path,
            final_title,
            final_highlight,
            is_video,
            workdir,
            design,
        )
    except (RuntimeError, FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": f"cover composition failed: {exc}"}

    video_path = Path(result["video_path"])
    poster_path = Path(result["poster_path"])
    duration_s = float(result.get("duration_s") or 0.0)
    if duration_s and not (_MIN_COVER_S - 0.5 <= duration_s <= _MAX_COVER_S + 0.5):
        warnings.append(
            f"cover duration {duration_s:.2f}s is outside the "
            f"{_MIN_COVER_S:g}-{_MAX_COVER_S:g} s budget"
        )

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
# Default instruction (mirrored in skills/agents/first_page_visual.md - the
# on-disk file wins at build time; this string is the fallback if it is gone).
# ---------------------------------------------------------------------------

DEFAULT_INSTRUCTION = """\
# First-Page Visual Agent

You build the COVER (slide 1) of an Instagram carousel: a short (4-15 second),
1080x1350 (4:5) video SOURCED from the news update itself. You never touch any
other slide, never write body copy or captions, and never AI-generate media.

## Context (injected from session state)

- News item: {news_item?}
- Carousel plan: {carousel_plan?}
- Current cover (empty on the first pass): {cover?}
- REWORK FEEDBACK - when present this is the human reviewer's correction and
  OVERRIDES everything else: {rework_feedback?}
- Distilled feedback from past runs: {recent_feedback_notes?}

## Hard rules

1. The cover is NEVER AI-generated. It is sourced from the update: the
   announcement/event clip (trimmed into the cover window), or - fallback -
   the update's own image (poster, paper screenshot, product UI, blog hero)
   turned into a 6 s slow-zoom cover video. Only when NOTHING sourced exists
   anywhere: a plain drawn dark background (create_placeholder_background).
2. Cover ONLY. Do not create, modify, or discuss body slides or the CTA slide.
3. The title comes from the plan's hook_title and the highlighted phrase from
   hook_highlight. Only override them when rework feedback explicitly asks for
   a different title. The highlight must stay a VERBATIM substring of the
   title; keep the title to 9 words or fewer, and aim for 40 characters or
   fewer so it renders large. build_cover returns a warnings list - read it;
   a hook flagged as too wide has been shrunk and will not read in a feed.
4. You MUST finish by calling build_cover successfully - that is what saves
   the cover artifacts and records the CoverSpec for the rest of the pipeline.

## Workflow - the sourcing ladder (NEVER stop before rung 6)

1. Call find_source_clip to pick the best sourced media (video preferred).
   It scans the news media_urls, the source page, every page LINKED in the
   news body text, AND runs a live trend-aware visual search for the topic.
   It ranks current prominent imagery by topic relevance, official/source
   affinity, useful visual signals and generic-asset penalties. Inspect
   trend_search and image_candidates in the result; never choose an image
   merely because it was the first available URL. You may pass search_query
   to sharpen the hunt (e.g. "<product> launch keynote demo").
   When the news title is one or more URLs, derive a clean subject query from
   the research brief and URL slug, such as "Niu Lai animated film official
   still poster". Prefer attached official or trusted media pages over an
   unverified blog OG image. Reject text-heavy social/OG banners that merely
   repeat the article title; choose a subject-led photo or frame that visibly
   shows the real person, product, place, or event.
2. If it returned a video (is_video true): call download_and_trim with that
   URL to get a local short clip. If the download fails (403s are common on
   video hosts), try at most ONE more video: another plausible URL from
   media_urls or one re-call of find_source_clip with a sharper search_query.
3. When is_video is false (only an image was found, or the story's own photo
   was put first) or video downloads keep failing, use the ranked
   image_candidates list from find_source_clip. Start with image_url, which
   is the highest-scoring candidate, then try the next candidate when
   download_image rejects a low-resolution, extreme-aspect, unreadable, or
   unavailable asset. Prefer the newest source-grounded launch/demo/keynote/
   news visual that directly depicts the topic; reject generic stock art,
   logos, icons and merely available images. Never shrink, letterbox,
   pre-blur, or frame media yourself.
   - A non-empty video_url means find_source_clip held back someone else's
     video (a web-search, trend-search or cited-article hit) because a story
     page supplied a photo. Only when every image you inspect is rejected,
     call download_and_trim on video_url and inspect that video. One
     inspection is always kept for it, even after the other 6 are used.
4. LOOK before you use it. Call inspect_cover_media on every downloaded
   candidate (for a video, pass source_path when download_and_trim returned
   one, otherwise clip_path). URLs and page text cannot tell you what a
   picture shows; this can. A playable video is not automatically a good
   cover: a screen recording of a PDF, a web page, a slide deck, a news
   anchor talking, or another channel's branded report makes a weak cover
   even when it is "the official video".
   - verdict "use" on a video: ALWAYS call retrim_clip with
     media_path=retrim_from and start_s=best_start_s, then build from the
     new clip_path. That makes the approved frame the cover's first frame
     and poster, ends the clip just before the footage cuts to another shot,
     and lets build_cover apply the inspection's subject crop. When the
     approved shot is shorter than the cover minimum, retrim_clip holds its
     last frame (held_s above 0) instead of running into the next shot.
   - verdict "use" on an image: build from it directly.
   - verdict "reject": move to the next candidate (the next distinct image
     in image_candidates, then video_url or another video) and inspect that
     one. A strong still beats a weak video.
   - problems containing "ai_illustration": the picture is AI-generated or
     a drawn illustration. Never use it, not even as a fallback; build_cover
     refuses it.
   - When every candidate you inspected is rejected and inspections are
     left, search again before settling: call find_source_clip with a
     search_query naming the story's people, product or place from the
     research brief plus "photo" (for example "Jane Doe John Roe researchers
     photo"), download its new top image and inspect it.
   - You have at most 6 inspections per run, plus the one kept for a video
     downloaded from video_url. When they run out, or every candidate was
     rejected, use the candidate with the highest score, but only if it
     scored 3 or more and was not flagged ai_illustration. Below 3 it is a
     text card, a document or an unrelated graphic, and a plain cover reads
     better: use create_placeholder_background. build_cover refuses media
     scored below 3.
   - If inspect_cover_media fails (ok false), continue with the best
     candidate you have; the check is a help, never a blocker.
5. Only if there is NO image_url anywhere and downloads all failed: call
   create_placeholder_background and use its path as the image.
6. ALWAYS call build_cover with the local media path, is_video set
   accordingly, and source_media_url set to the original URL for provenance
   (empty for the placeholder). Leave focus_x / focus_y / focus_w / focus_h
   at 0: build_cover applies the inspection by itself - the logo-free area
   to every clip cut from the inspected file, and the subject crop (a
   full-width band for wide group shots) to the inspected image whatever
   its verdict, or to a video clip retrimmed at best_start_s after a "use"
   verdict. Keep use_inspection true; a crop the reviewer rejects is fixed
   as described under Rework. Leave title and highlight empty
   so the plan's hook is used. The cover MUST be created on every run - a
   text-only cover on the placeholder background is the worst acceptable
   outcome, no cover at all is never acceptable.
7. Finish with a one-paragraph summary: which media you used (URL and origin
   - media_urls / source_page / body_page / research_page / trend_search /
   web_search / placeholder),
   sourced clip vs image vs placeholder, the inspection verdict and score
   (and what you rejected and why), final duration, and the artifact
   filenames. If you used the placeholder, say so explicitly so the reviewer
   knows no sourced media existed.

## Failure handling

- Tools report failures as ok=false with an error message instead of crashing.
  Read the error, then try the next-best candidate (another video URL, then
  the best image, then the placeholder background).
- NEVER finish without a successful build_cover call.

## Rework

When rework feedback is present, treat it as your highest-priority
instruction and rebuild the cover accordingly:

- "different moment / wrong part of the clip" - call retrim_clip on the
  source_path kept from download_and_trim with a new start_s (or download a
  different candidate URL), then rebuild. The stored subject crop belongs to
  the frame that was inspected, so a clip opening elsewhere gets the normal
  crop; to crop the new moment, call inspect_cover_media on the new
  clip_path first and follow rung 4.
- "crop is wrong / subject cut off or zoomed too far" - call build_cover
  again with an explicit focus_x / focus_y / focus_w / focus_h box (0-1
  fractions of the frame) around EVERY subject the cover must show: start
  from the inspection's focus box and widen it to take in whoever was cut
  off. For a group that fills the frame, a box of nearly the whole frame
  (0.02, 0.05, 0.96, 0.9) lays the whole group across the top. Do not use
  use_inspection=false for this: the renderer's normal crop fills the cover
  with one face of a group. Use it only when one subject fills the frame and
  the reviewer wants the zoom gone.
- "title / wording is off" - call build_cover with explicit title and
  highlight overrides (highlight must remain a verbatim substring).
- "bad image / wrong media" - pick the next ranked image_candidates entry or
  rerun find_source_clip with a sharper topic + launch/demo/current query;
  never reuse the same merely available image. Inspect the new candidate with
  inspect_cover_media, then rebuild.
- A video being playable is not proof that it is relevant. Reject search hits
  whose title has no distinctive person, company, product, or event term from
  the story (for example, unrelated trending anime for a hardware story).

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
        model=resolve_role_model("utility"),
        description=(
            "Builds the carousel's cover (slide 1): a short 1080x1350 video "
            "sourced from the news update (never AI-generated) - announcement "
            "clip, page-scraped or web-searched media, image fallback, or a "
            "drawn placeholder as last resort - composited with the "
            "cover overlay and the plan's hook title."
        ),
        instruction=instruction,
        tools=[
            FunctionTool(find_source_clip),
            FunctionTool(download_and_trim),
            FunctionTool(download_image),
            FunctionTool(create_placeholder_background),
            FunctionTool(retrim_clip),
            FunctionTool(inspect_cover_media),
            FunctionTool(build_cover),
        ],
        # Orchestrator-driven pipeline node: never LLM-transfer elsewhere.
        # (Both flags True + no sub_agents selects SingleFlow, so
        # transfer_to_agent is never offered to the model.)
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )
