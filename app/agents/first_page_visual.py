"""First-Page Visual agent - builds the sourced cover video (slide 1).

The cover is a 4-8 second 1080x1350 video composed from media SOURCED from the
news update itself (never AI-generated): the announcement/event clip, or - as a
fallback - the update's best image turned into a 6 s slow-zoom video. When no
sourced picture works, a credited free-licensed Wikimedia Commons photo of the
story's subject comes next; the plain drawn background is the last resort and
is flagged to the reviewer. The configured cover overlay - optional - plus the
plan's hook title in the selected design's exact text and highlight colors are
composited on top by ``app.tools.media_tools.compose_cover``.

The agent's tools write the final :class:`~app.schemas.CoverSpec` into session
state under ``K_COVER`` and save the rendered video + poster into the artifact
service - the agent itself has no ``output_schema`` (in ADK 2.7.0 tool-using
agents write state from inside tools via ``tool_context.state``).

Exposes :func:`build_first_page_visual_agent`.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import math
import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote, urlsplit

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.tools import FunctionTool, ToolContext
from google.genai import types
from pydantic import ValidationError

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
    K_COVER_DEADLINE,
    K_DESIGN,
    K_NEWS_ITEM,
    K_PLAN,
    K_RESEARCH,
    K_REWORK_FEEDBACK,
    K_RUN_ID,
    K_TIME_WINDOW,
    K_VERDICT,
    get_model,
    set_model,
)
from app.text_rules import require_no_em_dash
from app.tools import cover_vision, media_tools, reference_photos

logger = logging.getLogger(__name__)

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
# Where each candidate URL came from (origin, page, and for a Wikimedia photo
# its credit and licence), and which downloaded file came from which URL. Kept
# in session state so the judge sees the provenance and the credit reaches the
# caption, also after a rework round.
_K_CANDIDATES = "cover_media_candidates"  # {_url_key: {origin, context_url, credit, licence, subject, about}}
# {media key: {url, path, origin, context_url, credit, licence, subject, about}}
_K_SOURCES = "cover_media_sources"
# Per-step budgets and memory. ADK keeps temp: state across the child agents of
# one invocation, so _reset_step_state clears these when the step starts, or a
# QA re-run of the cover would begin with its budgets already spent. In the
# BBC run find_source_clip was called about 21 times, each re-running a billed
# trend search, until the 20-minute step timeout.
_MAX_SOURCE_SEARCHES = 3
_MAX_REFERENCE_LOOKUPS = 3
# Reference photos come after the regular inspections, so two looks are kept
# for files downloaded from them, the way one is kept for a held-back video.
_MAX_REFERENCE_INSPECTIONS = 2
_K_FSC_CALLS = "temp:cover_fsc_calls"
_K_FSC_CACHE = "temp:cover_fsc_cache"  # {"results": {query: result}, "last": query, "auto_reference": bool}
_K_PAGE_CACHE = "temp:cover_page_cache"  # media_tools.find_source_clip's page_cache
_K_FAILED_URLS = "temp:cover_failed_urls"
_K_REFERENCE_TRIED = "temp:cover_reference_tried"
_K_REFERENCE_CALLS = "temp:cover_reference_calls"
_K_REFERENCE_URLS = "temp:cover_reference_urls"  # URLs find_reference_photo returned
_K_REFERENCE_FILES = "temp:cover_reference_files"  # media keys downloaded from them
_K_REFERENCE_INSPECTED = "temp:cover_reference_inspected"
_K_OFFICIAL_SITES = "temp:cover_official_sites"  # {"subjects": [...], "sites": [...]}
_STEP_STATE: dict[str, Any] = {
    _K_FSC_CALLS: 0,
    _K_FSC_CACHE: None,
    _K_PAGE_CACHE: None,
    _K_FAILED_URLS: None,
    _K_REFERENCE_TRIED: None,
    _K_REFERENCE_CALLS: 0,
    _K_REFERENCE_URLS: None,
    _K_REFERENCE_FILES: None,
    _K_REFERENCE_INSPECTED: 0,
    _K_OFFICIAL_SITES: None,
}
# What the last cover was built from, and the media a human reviewer turned
# down. Session state, so a rework round knows them: a rejected cover used
# to stay best_so_far and was rebuilt round after round.
_K_BUILT = "cover_built_media"  # {"keys": [media keys of the built file and its inspected source]}
_K_REVIEWER_REJECTED = "cover_reviewer_rejected"  # media keys
_K_SHARE_ALIKE_SKIPPED = "cover_share_alike_skipped"  # int, for the review notice
# A text graphic (card, chart, slide) scored below this is only built from
# once the reference rung was tried and no real or official picture exists.
_MIN_FALLBACK_SCORE = 3
# The step's clock (K_COVER_DEADLINE, stamped by the orchestrator). Every
# tool's thread is awaited for at most its cap and what is left of the
# budget; in the BBC run nothing bounded the step and it hit the 20-minute
# timeout with no cover. Below the build reserve the slow tools (search,
# reference lookup, video download) refuse, and the ladder refusals that
# wait for the reference rung are lifted, so the agent can still build.
_BUILD_RESERVE_S = 300.0
_SEARCH_CAP_S = 150.0
# find_source_clip keeps to its own budget; the step stops waiting this much later.
_SEARCH_SLACK_S = 15.0
_REFERENCE_CAP_S = 40.0
_DOWNLOAD_CAP_S = 45.0
_TRIM_CAP_S = 120.0
_JUDGE_CAP_S = 60.0
_OFFICIAL_SITES_CAP_S = 8.0
# Story names the official check reads (the reference rung reads four).
_OFFICIAL_SUBJECTS = 8
# A reviewer's rejection turns the built picture down only where it finds
# fault with the picture: "Love the photo, keep it. Just shorten the
# headline" is about the words, and took the picture they liked out of
# best_so_far. Read clause by clause: a picture word, a fault word, and no
# unnegated praise or keep. A crop or a moment of the clip is fixed on the
# same picture. A link is always the reviewer's own picture to use instead.
_PICTURE_WORD_RE = re.compile(
    r"\b(?:image|images|picture|pictures|pic|pics|photo|photos|video|videos|clip|footage"
    r"|background|visual|visuals)\b|(?<!social )\bmedia\b",
    re.I,
)
_PICTURE_FAULT_RE = re.compile(
    r"\b(?:wrong|bad|poor|ugly|awful|terrible|blurry|blurred|pixelated|grainy|irrelevant"
    r"|unrelated|boring|generic|stock|weak|misleading|fake|cartoon|ai|replace|replaced|swap"
    r"|different|change|another|instead|better|remove|not|never)\b|n['’]t\b",
    re.I,
)
_PICTURE_KEPT_RE = re.compile(
    r"\b(?:keep|love|loved|like|liked|great|good|nice|fine|perfect|crop|cropped|cropping"
    r"|zoom|zoomed|framing|framed|moment|timestamp|frame|cut off)\b",
    re.I,
)
_CLAUSE_RE = re.compile(r"[.!?;,\n]+|\s(?:but|however|although|though)\s", re.I)
_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.I)


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


def _from_reference(tool_context: ToolContext, media_path: str) -> bool:
    """True when this media, or a file it was cut from, is a reference photo."""
    files = set(tool_context.state.get(_K_REFERENCE_FILES) or [])
    return any(key in files for key, _ in _lineage_chain(tool_context, media_path))


def _is_placeholder(media_path: str | Path) -> bool:
    """True for the drawn plain background from create_placeholder_background."""
    return Path(str(media_path)).name.lower() == media_tools.PLACEHOLDER_NAME.lower()


def _url_key(url: str) -> str:
    """One spelling per URL, so the agent's copy finds the recorded one.

    The agent re-types URLs: '%28' becomes '(' and utm_* tags come and go,
    and a Wikimedia photo then lost its credit on the way to the caption.
    """
    text = unquote(str(url or "").strip())
    return media_tools._page_key(text) if text else ""


# ---------------------------------------------------------------------------
# The step's clock
# ---------------------------------------------------------------------------


def _time_left(tool_context: ToolContext) -> Optional[float]:
    """Seconds until K_COVER_DEADLINE, or None when no deadline was stamped."""
    deadline = tool_context.state.get(K_COVER_DEADLINE)
    if deadline in (None, ""):
        return None
    try:
        return float(deadline) - time.time()
    except (TypeError, ValueError):
        return None


def _cap(tool_context: ToolContext, cap_s: float, reserve_s: float = 0.0) -> float:
    """How long one call may take: its cap, cut to what is left minus a reserve."""
    left = _time_left(tool_context)
    return cap_s if left is None else max(min(cap_s, left - reserve_s), 0.0)


def _out_of_time(tool_context: ToolContext) -> bool:
    """True once less than the build reserve is left: stop looking, build."""
    left = _time_left(tool_context)
    return left is not None and left < _BUILD_RESERVE_S


async def _within(timeout_s: float, func: Any, *args: Any, **kwargs: Any) -> Any:
    """Run a blocking call in a thread and wait at most ``timeout_s`` for it.

    A thread cannot be cancelled: one that runs over is left to its own
    network and ffmpeg timeouts, but the step no longer waits for it.

    Raises:
        TimeoutError: When the call did not finish in time (or no time is left).
    """
    if timeout_s <= 0:
        raise TimeoutError("no time left in this cover step")
    return await asyncio.wait_for(asyncio.to_thread(func, *args, **kwargs), timeout=timeout_s)


def _late_note() -> str:
    return (
        "time is nearly up for this cover step: build the cover now from "
        "best_so_far or the best picture you downloaded"
    )


# ---------------------------------------------------------------------------
# Verdicts, ranks and the ladder
# ---------------------------------------------------------------------------


def _kind_and_tier(record: dict) -> tuple[str, int]:
    """A stored verdict's kind and tier; records from before kinds get them derived."""
    verdict = str(record.get("verdict") or "reject")
    problems = list(record.get("problems") or [])
    kind = str(record.get("kind") or "") or cover_vision.kind_from_problems(problems, verdict)
    tier = record.get("tier")
    if not isinstance(tier, int):
        tier = cover_vision.tier_for(
            kind, str(record.get("origin") or ""), problems, record.get("official"),
        )
    return kind, tier


def _rank(entry: dict) -> tuple[bool, bool, int, int]:
    """Approved first, then a score of 3 or more, then the tier, then the score.

    An approved official visual therefore beats a rejected real photo (the
    judge rejects real photos for a corner logo, and saw the official one
    fit), and a rejected photo scored 1 no longer outranks a chart scored 5.
    """
    score = int(entry.get("score") or 0)
    return (entry["verdict"] == "use", score >= _MIN_FALLBACK_SCORE, entry["tier"], score)


def _reviewer_urls(tool_context: ToolContext) -> set[str]:
    """URL keys of every link in the rework feedback (the reviewer's own images)."""
    feedback = str(tool_context.state.get(K_REWORK_FEEDBACK) or "")
    return {_url_key(url.rstrip(".,;:!?")) for url in _URL_RE.findall(feedback)}


def _from_reviewer(tool_context: ToolContext, media_path: str) -> bool:
    """True when this media was downloaded from a link the reviewer gave."""
    url = str(_provenance(tool_context, media_path).get("url") or "")
    return bool(url) and _url_key(url) in _reviewer_urls(tool_context)


def _rejected_by_reviewer(tool_context: ToolContext, media_path: str) -> bool:
    """True when a human reviewer turned down this media (or what it was cut from)."""
    rejected = set(tool_context.state.get(_K_REVIEWER_REJECTED) or [])
    return any(key in rejected for key, _ in _lineage_chain(tool_context, media_path))


def _judged_family(tool_context: ToolContext) -> tuple[dict[str, dict], dict[str, dict]]:
    """Every tier-0 verdict, spread over the download it judged.

    A verdict is stored on the file that was inspected, but AI art is AI art
    in every copy: the untrimmed video a judged clip was cut from, and the
    same URL downloaded a second time, stayed buildable (the plain-background
    refusal even pointed the agent at that video). An ai_art verdict covers
    the judged file, every file it was cut from and the URLs they came from.
    An unusable one covers only the files it shares its footage with
    (download_and_trim's clip and the source it kept, but not the source of
    a moment retrim_clip picked) and their URLs. Clips cut from a covered
    file are covered through their lineage.

    Returns:
        ``(by media key, by _url_key)``, each mapping to the verdict record.
    """
    state = tool_context.state
    verdicts = state.get(_K_VERDICTS) or {}
    lineage = state.get(_K_LINEAGE) or {}
    sources = state.get(_K_SOURCES) or {}
    by_key: dict[str, dict] = {}
    by_url: dict[str, dict] = {}
    for judged, record in verdicts.items():
        if not isinstance(record, dict):
            continue
        kind, tier = _kind_and_tier(record)
        if tier >= 1:
            continue
        key, seen = judged, set()
        while key and key not in seen:
            seen.add(key)
            by_key.setdefault(key, record)
            url = _url_key(str((sources.get(key) or {}).get("url") or ""))
            if url:
                by_url.setdefault(url, record)
            link = lineage.get(key) or {}
            if isinstance(link, str):  # recorded before start offsets were kept
                link = {"parent": link, "start_s": None}
            if kind != "ai_art" and link.get("start_s") is not None:
                break  # only this moment of the footage was judged
            key = _media_key(link["parent"]) if link.get("parent") else ""
    return by_key, by_url


def _tier0_verdict(tool_context: ToolContext, media_path: str) -> Optional[dict]:
    """The tier-0 verdict covering this media (see :func:`_judged_family`), or None."""
    by_key, by_url = _judged_family(tool_context)
    if not by_key:
        return None
    sources = tool_context.state.get(_K_SOURCES) or {}
    for key, _ in _lineage_chain(tool_context, media_path):
        if key in by_key:
            return by_key[key]
        url = _url_key(str((sources.get(key) or {}).get("url") or ""))
        if url and url in by_url:
            return by_url[url]
    return None


def _judged_url(tool_context: ToolContext, url: str) -> Optional[dict]:
    """The tier-0 verdict on a download of this URL, unless the reviewer linked it."""
    key = _url_key(url)
    if not key or key in _reviewer_urls(tool_context):
        return None
    return _judged_family(tool_context)[1].get(key)


def _judged_refusal(record: dict) -> str:
    """Why media under a tier-0 verdict is never built or downloaded again."""
    kind, _ = _kind_and_tier(record)
    if kind == "ai_art":
        return (
            "the inspection judged this media (or another copy or cut of the same "
            f"download) AI-generated art made by a third party ({_AI_ILLUSTRATION}); "
            "the cover is never AI-generated - build from the next candidate or "
            "best_so_far, or call find_reference_photo"
        )
    problems = ", ".join(record.get("problems") or []) or kind or "no reason given"
    return (
        f"this media (or another copy or cut of the same download) was judged "
        f"unusable ({problems}) - build from the next candidate or best_so_far, "
        "or call find_reference_photo"
    )


def _inspected_pictures(tool_context: ToolContext) -> list[dict]:
    """Every inspected picture a cover could still be built from (tier 1 and up).

    Media the reviewer turned down is left out, and so is media a tier-0
    verdict covers, such as a clip cut from media judged tier 0 (build_cover
    refuses it whatever its own verdict, so offering it would also block the
    last rung).
    """
    pictures: list[dict] = []
    verdicts = tool_context.state.get(_K_VERDICTS) or {}
    for key, record in verdicts.items():
        if not isinstance(record, dict) or _is_placeholder(key) or not Path(key).exists():
            continue
        kind, tier = _kind_and_tier(record)
        if tier < 1 or _rejected_by_reviewer(tool_context, key) or _judged_unusable(tool_context, key):
            continue
        is_video = record.get("is_video")
        if not isinstance(is_video, bool):
            is_video = Path(key).suffix.lower() not in media_tools.IMAGE_EXTS
        path = str(record.get("path") or key)
        pictures.append({
            "path": path,
            "is_video": is_video,
            "url": str(record.get("url") or ""),
            "kind": kind,
            "tier": tier,
            "score": int(record.get("score") or 0),
            "verdict": "use" if record.get("verdict") == "use" else "reject",
            "origin": str(record.get("origin") or ""),
            # For a video: build_cover cuts the judged moment out of
            # retrim_from itself, so the approved frame opens the cover.
            "best_start_s": round(float(record.get("best_start_s") or 0.0), 2) if is_video else 0.0,
            "retrim_from": path if is_video else "",
        })
    return pictures


def _best_so_far(tool_context: ToolContext) -> Optional[dict]:
    """The strongest picture inspected so far, by :func:`_rank`, or None."""
    pictures = _inspected_pictures(tool_context)
    return max(pictures, key=_rank) if pictures else None


def _describe(entry: dict) -> str:
    return f"{entry['path']} ({entry['kind']}, tier {entry['tier']}, {entry['verdict']}, score {entry['score']})"


def _downloaded_pictures(tool_context: ToolContext) -> list[str]:
    """Downloaded files a cover could still be built from, newest first.

    Inspected or not: a reference photo downloaded but never inspected used
    to leave the plain background buildable next to it.
    """
    sources = tool_context.state.get(_K_SOURCES) or {}
    return [
        str((sources.get(key) or {}).get("path") or key)
        for key in reversed(list(sources.keys()))
        if not _is_placeholder(key)
        and Path(key).exists()
        and not _rejected_by_reviewer(tool_context, key)
        and not _judged_unusable(tool_context, key)
    ]


def _cover_refusal(tool_context: ToolContext, media_path: str) -> Optional[str]:
    """Why build_cover must not use this media, or None when it may.

    The ladder, strongest first: approved or real/official pictures, text
    graphics that scored 3 or more, a Wikimedia reference photo, weaker text
    graphics, and last the plain background. Two AI-model launch runs shipped
    that plain background although the companies' own artwork had been
    downloaded, and nothing flagged it. Checked whatever use_inspection says:
    these are sourcing rules, not crop preferences. Media that was never
    inspected stays buildable (the check is a help, never a blocker), unless
    a tier-0 verdict on the same download covers it (:func:`_judged_family`),
    and so does anything downloaded from the reviewer's own link. With less
    than the build reserve left, nothing waits for the reference rung any
    more.
    """
    tried = bool(tool_context.state.get(_K_REFERENCE_TRIED)) or _out_of_time(tool_context)
    if _is_placeholder(media_path):
        best = _best_so_far(tool_context)
        if best is not None:
            return (
                "the plain background is the last resort and a picture was already "
                f"inspected: build from best_so_far {_describe(best)}"
            )
        downloaded = _downloaded_pictures(tool_context)
        if downloaded:
            return (
                "the plain background is the last resort and a picture was already "
                f"downloaded: build from {downloaded[0]} (inspect it first if an "
                "inspection is left)"
            )
        if not tried:
            return (
                "the plain background is the last resort: call find_reference_photo "
                "first (a credited free-licensed photo of the story's people, "
                "organisation or place), then download_image and inspect_cover_media "
                "what it returns"
            )
        return None
    if _from_reviewer(tool_context, media_path):
        return None

    judged = _tier0_verdict(tool_context, media_path)
    if judged is not None:
        return _judged_refusal(judged)
    record, _ = _verdict_for(tool_context, media_path)
    if isinstance(record, dict):
        kind, tier = _kind_and_tier(record)
        score = int(record.get("score") or 0)
        if tier == 1 and score < _MIN_FALLBACK_SCORE:
            better = [p for p in _inspected_pictures(tool_context) if p["tier"] >= 2]
            if better:
                return (
                    f"this {kind} scored {score}, and a real or official picture was "
                    f"inspected: build from it instead, {_describe(max(better, key=_rank))}"
                )
            if not tried:
                return (
                    f"this {kind} scored {score} (a text card, chart or slide reads "
                    "badly as a cover): call find_reference_photo first for a credited "
                    "photo of the story's people, organisation or place, and build "
                    "from this only when that finds nothing better"
                )
    return None


def _provenance(tool_context: ToolContext, media_path: str) -> dict:
    """Where this media (or the file it was cut from) was downloaded from."""
    sources = tool_context.state.get(_K_SOURCES) or {}
    for key, _ in _lineage_chain(tool_context, media_path):
        record = sources.get(key)
        if isinstance(record, dict):
            return record
    return {}


def _provenance_text(provenance: dict) -> str:
    """One short line for the judge: the page, the origin, and a Wikimedia subject."""
    page = str(provenance.get("context_url") or provenance.get("url") or "")
    if not page:
        return ""
    parts = urlsplit(page)
    where = f"{parts.netloc}{parts.path[:80]}" if parts.netloc else page[:80]
    origin = str(provenance.get("origin") or "")
    text = f"{where} ({origin or 'unknown origin'})"
    if origin == "wikimedia":
        # 'Xiaomi: Chinese electronics company', not just a name that a
        # warship or a radio technique may share.
        subject = str(provenance.get("about") or provenance.get("subject") or "").strip()
        text += f", Wikimedia Commons photo of {subject}" if subject else ", Wikimedia Commons photo"
    return text[:240]


def _story_text(news: Optional[NewsItem], research: Optional[ResearchBrief]) -> str:
    """The story's own words, for telling the subject company's pages from others."""
    parts: list[str] = []
    if news is not None:
        parts += [news.title, news.summary]
    if research is not None:
        parts.append(research.summary)
        parts += [fact.fact for fact in research.key_facts[:12]]
    return " ".join(part for part in parts if part)


# "original" is the URL as found when find_source_clip offered a cover-size
# variant of it: download_image falls back to it when the variant fails.
_PROVENANCE_FIELDS = ("origin", "context_url", "credit", "licence", "subject", "about", "original")


def _record_candidates(tool_context: ToolContext, entries: list[dict]) -> None:
    """Remember where each offered URL came from, for download_image to pick up.

    Keyed by :func:`_url_key`, so the credit survives the agent re-typing it.
    """
    candidates = dict(tool_context.state.get(_K_CANDIDATES) or {})
    changed = False
    for entry in entries:
        key = _url_key(str(entry.get("url") or ""))
        if not key:
            continue
        fields = {field: str(entry.get(field) or "") for field in _PROVENANCE_FIELDS}
        if key in candidates and not entry.get("credit"):
            # A Wikimedia entry carries the credit; never let a bare record
            # replace it, only fill what it left empty (a video's page).
            known = dict(candidates[key])
            filled = {field: value for field, value in fields.items() if value and not known.get(field)}
            if filled:
                candidates[key] = {**known, **filled}
                changed = True
            continue
        candidates[key] = fields
        changed = True
    if changed:
        tool_context.state[_K_CANDIDATES] = candidates


def _known_candidate(tool_context: ToolContext, url: str) -> dict:
    """What find_source_clip or find_reference_photo said about this URL."""
    return dict((tool_context.state.get(_K_CANDIDATES) or {}).get(_url_key(url)) or {})


def _record_download(tool_context: ToolContext, url: str, paths: list[str]) -> None:
    """Tie downloaded files to the URL's provenance (and to the reference rung)."""
    url = url.strip()
    known = _known_candidate(tool_context, url)
    record = {"url": url, **{field: str(known.get(field) or "") for field in _PROVENANCE_FIELDS}}
    keys = [_media_key(path) for path in paths if path]
    sources = dict(tool_context.state.get(_K_SOURCES) or {})
    for path in paths:
        if path:
            sources[_media_key(path)] = {**record, "path": str(path)}
    tool_context.state[_K_SOURCES] = sources
    if _url_key(url) in (tool_context.state.get(_K_REFERENCE_URLS) or []):
        files = list(tool_context.state.get(_K_REFERENCE_FILES) or [])
        tool_context.state[_K_REFERENCE_FILES] = files + [k for k in keys if k not in files]


def _record_failed(tool_context: ToolContext, url: str) -> None:
    """Remember a URL that failed to download, so it is not offered again."""
    failed = list(tool_context.state.get(_K_FAILED_URLS) or [])
    key = _url_key(url)
    if key and key not in failed:
        tool_context.state[_K_FAILED_URLS] = failed + [key]


def _news_dict(tool_context: ToolContext) -> Optional[dict]:
    """Load the queued news item from session state as a plain dict."""
    news = get_model(tool_context.state, K_NEWS_ITEM, NewsItem)
    if news is None:
        return None
    return news.model_dump(mode="json")


def _story_subjects(tool_context: ToolContext) -> list[str]:
    """The story's people, organisation, product and place, for the reference rung."""
    research = get_model(tool_context.state, K_RESEARCH, ResearchBrief)
    plan = get_model(tool_context.state, K_PLAN, CarouselPlan)
    return reference_photos.story_subjects(
        _news_dict(tool_context),
        research.model_dump(mode="json") if research else None,
        plan.model_dump(mode="json") if plan else None,
    )


def _dead_urls(tool_context: ToolContext) -> set[str]:
    """URL keys never offered again: failed downloads in this step, and any
    download the picture check judged third-party AI art or unusable."""
    judged = set(_judged_family(tool_context)[1]) - _reviewer_urls(tool_context)
    return set(tool_context.state.get(_K_FAILED_URLS) or []) | judged


def _without_failed(tool_context: ToolContext, result: dict) -> dict:
    """Drop candidates that already failed to download in this step, or were judged tier 0.

    The BBC run offered the same 240 px thumbnails on every re-call, and the
    agent downloaded them again each time.
    """
    failed = _dead_urls(tool_context)
    if not failed:
        return result

    def gone(value: Any) -> bool:
        return _url_key(str(value or "")) in failed

    candidates = result.get("image_candidates")
    if isinstance(candidates, list):
        result["image_candidates"] = [c for c in candidates if not gone((c or {}).get("url"))]
    if gone(result.get("image_url")):
        top = (result.get("image_candidates") or [{}])[0] or {}
        result["image_url"] = str(top.get("url") or "")
        result["image_origin"] = str(top.get("origin") or "")
    if gone(result.get("video_url")):
        result.update(video_url="", video_duration_s=0.0, video_origin="")
    if gone(result.get("url")):
        # The pick already failed: the best remaining image takes its place.
        result.update(
            url=str(result.get("image_url") or ""),
            origin=str(result.get("image_origin") or ""),
            is_video=False,
            duration_s=0.0,
        )
        if not result["url"]:
            result["found"] = False
            result["note"] = (
                f"{result.get('note') or ''} Every candidate here already failed "
                "to download or was judged unusable."
            ).strip()
    return result


def _empty_clip_result(note: str) -> dict:
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
        "note": note,
    }


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
        ranked images in order when video downloads fail. Images that already
        failed to download in this step, and media the picture check judged
        AI art or unusable, are left out.
        calls_left: searches left in this step (3 per step). Re-call only
        with a sharper, DIFFERENT search_query; the same query returns the
        same result for free, and once the budget is used the last result
        comes back. Each call keeps to about two and a half minutes, and
        near the end of the step's time it only returns the last result.
        When no story media is found, found is false, and free-licensed
        Wikimedia Commons reference photos of the story's subjects may be
        returned instead (origin 'wikimedia'; their credit goes into the
        caption): download, inspect, then build. When there are none, call
        find_reference_photo.
    """
    news = _news_dict(tool_context)
    state = tool_context.state
    calls = int(state.get(_K_FSC_CALLS) or 0)
    if news is None:
        result = _empty_clip_result("no news item in session state (K_NEWS_ITEM missing)")
        result["calls_left"] = max(_MAX_SOURCE_SEARCHES - calls, 0)
        return result

    cache = dict(state.get(_K_FSC_CACHE) or {})
    results: dict[str, dict] = dict(cache.get("results") or {})
    query_key = " ".join(search_query.split()).lower()
    if query_key in results:
        # The same search in the same step finds the same media: no new call.
        result = copy.deepcopy(results[query_key])
    elif calls >= _MAX_SOURCE_SEARCHES or (
        _cap(tool_context, _SEARCH_CAP_S, _BUILD_RESERVE_S) < min(10.0, _SEARCH_CAP_S)
    ):
        last = results.get(str(cache.get("last") or "")) or _empty_clip_result("")
        result = copy.deepcopy(last)
        result["note"] = (
            f"search budget used ({_MAX_SOURCE_SEARCHES} calls this step): call "
            "find_reference_photo, or build from best_so_far"
            if calls >= _MAX_SOURCE_SEARCHES else _late_note()
        )
        result = _without_failed(tool_context, result)
        result["calls_left"] = max(_MAX_SOURCE_SEARCHES - calls, 0)
        return result
    else:
        calls += 1
        state[_K_FSC_CALLS] = calls
        research = get_model(state, K_RESEARCH, ResearchBrief)
        research_sources = list(research.sources) if research else []
        # save_research_brief also merges these into the news item's media_urls;
        # passing them separately keeps them from counting as the story's own.
        research_media = list(research.media_candidates) if research else []
        window = state.get(K_TIME_WINDOW)
        extra: dict[str, Any] = {}
        if isinstance(window, dict) and (window.get("start") or window.get("oldest")):
            # Only a request that asked about a time ("this week") narrows the
            # trend search; old sessions and topic-free runs search as before.
            extra["time_window"] = window
        # Pages scraped by an earlier call of this step are not fetched again.
        pages = dict(state.get(_K_PAGE_CACHE) or {})
        # Each candidate's page and pre-upgrade URL: the C1 keys give no page
        # for url and video_url, and without it Xiaomi's own demo video on
        # its CDN could not be told from a fan site's (so it was "AI art").
        found_on: dict[str, dict] = {}
        budget = _cap(tool_context, _SEARCH_CAP_S, _BUILD_RESERVE_S)
        try:
            result = await _within(
                budget + _SEARCH_SLACK_S,
                media_tools.find_source_clip,
                news,
                search_query,
                research_sources,
                research_media,
                subjects=_story_subjects(tool_context),
                page_cache=pages,
                budget_s=budget,
                provenance=found_on,
                **extra,
            )
        except TimeoutError:
            result = _empty_clip_result(
                "the search ran out of time: try the candidates you have, "
                "find_reference_photo, or build from best_so_far"
            )
        result = dict(result)
        state[_K_PAGE_CACHE] = pages

        def with_page(fields: dict) -> dict:
            found = found_on.get(str(fields.get("url") or "")) or {}
            return {
                **fields,
                "context_url": fields.get("context_url") or found.get("context_url") or "",
                "original": found.get("original") or "",
            }

        _record_candidates(tool_context, [with_page(fields) for fields in (
            *(c for c in result.get("image_candidates") or [] if isinstance(c, dict)),
            {"url": result.get("url"), "origin": result.get("origin")},
            {"url": result.get("image_url"), "origin": result.get("image_origin")},
            {"url": result.get("video_url"), "origin": result.get("video_origin")},
        )])
        held_url = _url_key(str(result.get("video_url") or ""))
        if held_url:
            held = list(state.get(_K_HELD_VIDEOS) or [])
            if held_url not in held:
                state[_K_HELD_VIDEOS] = held + [held_url]

    result = _without_failed(tool_context, result)
    if result.get("found") is False and not cache.get("auto_reference") and not _out_of_time(tool_context):
        # Once per step: the free reference rung, so a story whose pages only
        # carry thumbnails still gets a real picture without another search.
        cache["auto_reference"] = True
        result = await _add_reference_photos(tool_context, result)
    results[query_key] = copy.deepcopy(result)
    cache.update(results=results, last=query_key)
    state[_K_FSC_CACHE] = cache
    result["calls_left"] = max(_MAX_SOURCE_SEARCHES - calls, 0)
    return result


async def _add_reference_photos(tool_context: ToolContext, result: dict) -> dict:
    """Fill an empty find_source_clip result with Wikimedia reference photos.

    Does not count as trying the reference rung: the agent still calls
    find_reference_photo (with broader subjects) before the plain background.
    """
    subjects = _story_subjects(tool_context)
    refs = await _reference_lookup(tool_context, subjects)
    if not refs:
        if subjects:
            result["note"] = (
                f"{result.get('note') or ''} A Wikimedia lookup for "
                f"{', '.join(subjects)} found nothing free-licensed either."
            ).strip()
        return result
    result.update(
        found=True,
        url=refs[0]["url"],
        is_video=False,
        duration_s=0.0,
        origin="wikimedia",
        image_url=refs[0]["url"],
        image_origin="wikimedia",
        image_candidates=refs[:5],
        note=(
            "no story media found; free-licensed reference photos of "
            f"{', '.join(subjects)} from Wikimedia Commons (credit goes into the "
            "caption) - download, inspect, then build"
        ),
    )
    return result


def _story_context(tool_context: ToolContext) -> str:
    """The story's words, for telling namesakes and official sites apart."""
    return _story_text(
        get_model(tool_context.state, K_NEWS_ITEM, NewsItem),
        get_model(tool_context.state, K_RESEARCH, ResearchBrief),
    )


async def _reference_lookup(tool_context: ToolContext, subjects: list[str]) -> list[dict]:
    """Run the Wikimedia lookup off the event loop and remember what it offered."""
    if not subjects:
        return []
    budget = _cap(tool_context, _REFERENCE_CAP_S, _BUILD_RESERVE_S / 2)
    report: dict = {}
    try:
        refs = await _within(
            budget + 10.0,
            reference_photos.reference_candidates,
            subjects,
            budget_s=budget,
            context=_story_context(tool_context),
            report=report,
        )
    except Exception as exc:  # noqa: BLE001 - a free helper; never lose the cover over it
        logger.warning("Reference photo lookup failed for %s: %s", subjects, exc)
        return []
    skipped = int(report.get("share_alike_skipped") or 0)
    if skipped > int(tool_context.state.get(_K_SHARE_ALIKE_SKIPPED) or 0):
        tool_context.state[_K_SHARE_ALIKE_SKIPPED] = skipped
    failed = _dead_urls(tool_context)
    refs = [
        r for r in refs or []
        if isinstance(r, dict) and r.get("url") and _url_key(r["url"]) not in failed
    ]
    if refs:
        _record_candidates(tool_context, refs)
        urls = list(tool_context.state.get(_K_REFERENCE_URLS) or [])
        tool_context.state[_K_REFERENCE_URLS] = urls + [
            key for key in (_url_key(r["url"]) for r in refs) if key not in urls
        ]
    return refs


async def find_reference_photo(
    subjects: list[str] = [],  # never mutated; ADK shows the default to the model
    *,
    tool_context: ToolContext,
) -> dict:
    """Find free-licensed Wikimedia Commons photos of the story's subjects.

    The rung between "no sourced picture works" and the plain background:
    real photos (Wikidata images of the story's people, organisation, its
    leader, or place, then Commons search), free-licensed and credited - the
    credit is added to the caption automatically. Prefer a photo of the
    organisation, product or place unless the story is about a person. Call
    download_image then inspect_cover_media on the top one or two, and build
    from best_so_far. At most 3 calls per step, and none near the end of
    the step's time.

    Args:
        subjects: Up to 4 names to look up, most specific first (people,
            organisation, product, place), e.g. ["Anthony Albanese",
            "Parliament House Canberra"]. Leave empty to use the story's own
            subjects; on a "Cover has no picture" rework pass broader ones
            (the country, city, organisation or sector).

    Returns:
        found (bool), subjects (the names looked up), image_candidates
        (url, origin 'wikimedia', score, reason, context_url, credit,
        licence, subject, about, role, width, height), note (what to do next).
    """
    state = tool_context.state
    # Set whatever happens next: the plain background waits for this rung.
    state[_K_REFERENCE_TRIED] = True
    wanted: list[str] = []
    for subject in subjects or []:
        name = " ".join(str(subject or "").split())
        if name and name.lower() not in (w.lower() for w in wanted):
            wanted.append(name)
    wanted = wanted[:4]
    calls = int(state.get(_K_REFERENCE_CALLS) or 0)
    if calls >= _MAX_REFERENCE_LOOKUPS or _out_of_time(tool_context):
        why = (
            f"reference budget used ({_MAX_REFERENCE_LOOKUPS} lookups this step)"
            if calls >= _MAX_REFERENCE_LOOKUPS else "time is nearly up for this cover step"
        )
        return {
            "found": False,
            "subjects": wanted,
            "image_candidates": [],
            "note": f"{why}: " + _fallback_advice(tool_context),
        }
    state[_K_REFERENCE_CALLS] = calls + 1
    if not wanted:
        wanted = _story_subjects(tool_context)
    refs = await _reference_lookup(tool_context, wanted)
    if refs:
        return {
            "found": True,
            "subjects": wanted,
            "image_candidates": refs,
            "note": (
                "free-licensed Wikimedia Commons photos (the credit goes into the "
                "caption): download_image then inspect_cover_media the top one or "
                "two, then build from best_so_far"
            ),
        }
    looked_up = ", ".join(wanted) or "the story's subjects"
    if calls == 0:
        note = (
            f"nothing free-licensed found for {looked_up}. Call find_reference_photo "
            "once more with broader subjects (the country, city, organisation or "
            "sector); if that finds nothing either, " + _fallback_advice(tool_context)
        )
    else:
        note = f"nothing free-licensed found for {looked_up}: " + _fallback_advice(tool_context)
    return {"found": False, "subjects": wanted, "image_candidates": [], "note": note}


def _fallback_advice(tool_context: ToolContext) -> str:
    best = _best_so_far(tool_context)
    if best is not None:
        return f"build from best_so_far {_describe(best)}"
    downloaded = _downloaded_pictures(tool_context)
    if downloaded:
        return f"build from the picture you downloaded, {downloaded[0]}"
    return (
        "build from the best candidate you downloaded, and only when no picture "
        "could be downloaded at all, from create_placeholder_background (the "
        "reviewer is warned)"
    )


async def download_and_trim(
    url: str, max_s: int = 0, min_s: int = 0, *, tool_context: ToolContext
) -> dict:
    """Download a video URL and trim it to a short silent H.264 cover clip.

    Works with direct video files and any yt-dlp-supported page (YouTube,
    Vimeo, X, ...). Long videos are section-downloaded, so this is safe on
    full-length keynotes. It waits at most two minutes, and near the end of
    the step's time it refuses (build from what you have).

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
    wait_s = _cap(tool_context, _TRIM_CAP_S, _BUILD_RESERVE_S / 2)
    if _out_of_time(tool_context) or wait_s < 10.0:
        return {"ok": False, "clip_path": "", "source_path": "", "error": _late_note()}
    judged = _judged_url(tool_context, url)
    if judged is not None:
        return {"ok": False, "clip_path": "", "source_path": "",
                "error": f"not downloaded again: {_judged_refusal(judged)}"}
    workdir = _run_workdir(tool_context)
    try:
        clip_path = await _within(
            wait_s,
            media_tools.download_and_trim,
            url,
            max_s or None,
            min_s or None,
            workdir,
        )
    except TimeoutError:
        _record_failed(tool_context, url)
        return {"ok": False, "clip_path": "", "source_path": "",
                "error": "the video download took too long; try an image candidate"}
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        _record_failed(tool_context, url)
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
    _record_download(tool_context, url, [str(clip), source_path])
    if _url_key(url) in (tool_context.state.get(_K_HELD_VIDEOS) or []):
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


async def _wikimedia_credit(tool_context: ToolContext, url: str) -> Optional[str]:
    """Credit a Wikimedia file that came without one; the reason it cannot be used, or None.

    A research page can link an upload.wikimedia.org picture directly, and it
    reached the cover with no credit (and a Wikipedia page's own files are
    often non-free).
    """
    if reference_photos.wikimedia_file(url) is None or _known_candidate(tool_context, url).get("credit"):
        return None
    try:
        found = await _within(
            _cap(tool_context, 15.0), reference_photos.file_credit, url, budget_s=10.0,
        )
    except Exception as exc:  # noqa: BLE001 - checked below as "unknown"
        logger.warning("Wikimedia credit lookup failed for %s: %s", url, exc)
        found = None
    if not found:
        return "its Wikimedia licence could not be checked, so it cannot be credited"
    if not found.get("reusable"):
        licence = found.get("licence") or "no licence"
        return f"its Wikimedia licence ({licence}) is not free to reuse on the cover"
    known = _known_candidate(tool_context, url)
    _record_candidates(tool_context, [{
        **known,
        "url": url,
        "origin": "wikimedia",
        "context_url": found.get("context_url") or known.get("context_url") or "",
        "credit": found["credit"],
        "licence": found.get("licence") or "",
        "subject": known.get("subject") or str(found.get("title") or "").split(":", 1)[-1],
    }])
    return None


async def download_image(url: str, *, tool_context: ToolContext) -> dict:
    """Download the news item's best still image (the cover fallback source).

    Use this only when no playable sourced video exists; build_cover will turn
    the image into a 6 s slow-zoom cover video. The URL's origin (and, for a
    Wikimedia photo, its credit) is remembered for the inspection and the
    caption; a Wikimedia file found some other way gets its credit looked up,
    and one that is not free to reuse is refused.

    Args:
        url: Direct image URL (from find_source_clip, find_reference_photo,
            the news media_urls, or a reviewer's feedback).

    Returns:
        On success: ok (true) and path (local image file).
        On failure: ok (false) and error (str); the URL is not offered again
        in this step. A picture the check already judged third-party AI art
        or unusable is not downloaded again.
    """
    wait_s = _cap(tool_context, _DOWNLOAD_CAP_S, 60.0)
    if wait_s < 5.0:
        # Not tried, so not remembered as failed.
        return {"ok": False, "path": "", "error": _late_note()}
    judged = _judged_url(tool_context, url)
    if judged is not None:
        return {"ok": False, "path": "", "error": f"not downloaded again: {_judged_refusal(judged)}"}
    refused = await _wikimedia_credit(tool_context, url)
    if refused:
        _record_failed(tool_context, url)
        return {"ok": False, "path": "", "error": f"not downloaded: {refused}; try the next candidate"}
    workdir = _run_workdir(tool_context)
    # find_source_clip offers a CDN's cover-size variant. Downloading the URL
    # as found tries that variant first and falls back to the original, which
    # a CDN refusing the rewritten size (a fixed-width service) used to lose.
    fetch = str(_known_candidate(tool_context, url).get("original") or "").strip() or url
    try:
        path = await _within(wait_s, media_tools.download_image, fetch, workdir)
    except TimeoutError:
        _record_failed(tool_context, url)
        return {"ok": False, "path": "", "error": "the download took too long; try the next candidate"}
    except (RuntimeError, OSError) as exc:
        _record_failed(tool_context, url)
        return {"ok": False, "path": "", "error": str(exc)}
    _record_download(tool_context, url, [str(path)])
    return {"ok": True, "path": str(path), "error": ""}


async def create_placeholder_background(*, tool_context: ToolContext) -> dict:
    """Build the plain drawn dark background (the very LAST resort).

    Use it only after find_reference_photo returned nothing that could be
    downloaded and no picture was inspected or downloaded: build_cover
    refuses it otherwise. A drawn (non-AI) dark gradient still the cover
    template + title composite onto, so the cover is ALWAYS created. The
    reviewer is warned that the cover has no picture. Feed the returned path
    to build_cover with is_video=false and say so in your summary.

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


def _official_subjects(tool_context: ToolContext) -> list[str]:
    """The story's names for the official check: up to eight, near-duplicates merged.

    The check only compares names (plus one cached Wikidata lookup), so it
    reads more of them than the reference rung does: in run-3c4107e390df
    OpenAI came fifth, behind both 'Mistral Small' and 'Mistral', and its own
    hero image on openai.com counted as nobody's. A name that starts with an
    earlier one's words ('Mistral Small' after 'Mistral') is merged into the
    shorter name, the one a website and a Wikidata item are named after.
    """
    research = get_model(tool_context.state, K_RESEARCH, ResearchBrief)
    plan = get_model(tool_context.state, K_PLAN, CarouselPlan)
    names = reference_photos.story_subjects(
        _news_dict(tool_context),
        research.model_dump(mode="json") if research else None,
        plan.model_dump(mode="json") if plan else None,
        limit=_OFFICIAL_SUBJECTS,
    )
    kept: list[str] = []
    for name in names:
        words = name.casefold().split()
        for index, other in enumerate(kept):
            known = other.casefold().split()
            if words[:len(known)] == known:
                break  # 'Mistral Small' after 'Mistral'
            if known[:len(words)] == words:
                kept[index] = name  # 'Mistral' after 'Mistral Small'
                break
        else:
            kept.append(name)
    return list(dict.fromkeys(kept))


async def _official_level(tool_context: ToolContext, pages: list[str], news: Optional[NewsItem]) -> str:
    """How surely the media's pages are the story subject's own (cover_vision.official_source).

    The subjects' official websites on Wikidata are looked up once per step,
    before the first check: a page named like a subject on some domain
    (xiaomi.eu, liquid.com) is exact only on the subject's own site, one that
    only resembles it (xiaomimimo.com) or names nobody (appmifile.com for
    Xiaomi) may still be on it (mi.com), and a known site elsewhere rules a
    namesake out.
    """
    if not pages:
        return ""
    state = tool_context.state
    subjects = _official_subjects(tool_context)
    publishers = [p for p in ((news.source_url, news.source_name) if news else ()) if p]
    cached = state.get(_K_OFFICIAL_SITES)
    if not isinstance(cached, dict) and subjects and not _out_of_time(tool_context):
        try:
            found = await _within(
                _cap(tool_context, _OFFICIAL_SITES_CAP_S + 4.0),
                reference_photos.official_sites,
                subjects,
                budget_s=_OFFICIAL_SITES_CAP_S,
                context=_story_context(tool_context),
                limit=_OFFICIAL_SUBJECTS,
            )
        except Exception as exc:  # noqa: BLE001 - a free helper; unknown stays a name match
            logger.warning("Official website lookup failed for %s: %s", subjects, exc)
            found = []
        cached = state[_K_OFFICIAL_SITES] = {"subjects": subjects, "sites": list(found or [])}
    sites = list((cached or {}).get("sites") or []) if isinstance(cached, dict) else []
    extra: dict[str, Any] = {"sites": sites} if sites else {}
    return cover_vision.official_level(
        cover_vision.official_source(pages, subjects, publishers=publishers, **extra)
    )


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
    inspected after the regular inspections are used up (once), and so are
    two photos downloaded from find_reference_photo.

    Args:
        media_path: Local video or image path.
        is_video: True for a video file.

    Returns:
        ok (bool), verdict ('use' | 'reject'), score (0-10), problems (list;
        'ai_illustration' means AI art made by a third party - never use it,
        build_cover refuses it), kind ('real_photo' | 'official_visual' |
        'text_graphic' | 'ai_art' | 'unusable') and tier (3 real photo, 2
        official visual from the company's own site or Wikimedia photo, 1
        text graphic, generic stock or an official-looking visual whose site
        is not verifiably the company's, 0 never used), official (the media
        came from the story's own company), reason, best_start_s and
        retrim_from (video: call retrim_clip with media_path=retrim_from and
        start_s=best_start_s so the approved frame opens the cover and
        becomes its poster), focus_x / focus_y / focus_w / focus_h (subject
        box as 0-1 fractions, all 0 when the renderer's normal crop already
        works), inspections_left, and best_so_far (the strongest picture
        inspected so far: path, is_video, url, kind, tier, score, verdict,
        origin, best_start_s, retrim_from; null when none). On failure ok is
        false and the cover should be built from best_so_far or the best
        candidate.
    """
    state = tool_context.state
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
            "inspections_left": _MAX_INSPECTIONS - int(state.get(_K_INSPECTIONS) or 0),
        }
    judge_s = _cap(tool_context, _JUDGE_CAP_S, 60.0)
    if judge_s < 10.0:
        return {
            "ok": False,
            "error": _late_note() + ". No inspection was used.",
            "inspections_left": _MAX_INSPECTIONS - int(state.get(_K_INSPECTIONS) or 0),
            "best_so_far": _best_so_far(tool_context),
        }
    used = int(state.get(_K_INSPECTIONS) or 0)
    held_reserve = (
        used >= _MAX_INSPECTIONS
        and not state.get(_K_HELD_INSPECTED)
        and _from_held_video(tool_context, media_path)
    )
    reference_reserve = (
        used >= _MAX_INSPECTIONS
        and not held_reserve
        and int(state.get(_K_REFERENCE_INSPECTED) or 0) < _MAX_REFERENCE_INSPECTIONS
        and _from_reference(tool_context, media_path)
    )
    if used >= _MAX_INSPECTIONS and not (held_reserve or reference_reserve):
        return {
            "ok": False,
            "error": "inspection budget used up; build the cover from best_so_far",
            "inspections_left": 0,
            "best_so_far": _best_so_far(tool_context),
        }
    if held_reserve:
        state[_K_HELD_INSPECTED] = True
    elif reference_reserve:
        state[_K_REFERENCE_INSPECTED] = int(state.get(_K_REFERENCE_INSPECTED) or 0) + 1
    else:
        used += 1
        state[_K_INSPECTIONS] = used
    left = _MAX_INSPECTIONS - used

    news = get_model(state, K_NEWS_ITEM, NewsItem)
    plan = get_model(state, K_PLAN, CarouselPlan)
    story = ""
    if news is not None:
        story = f"{news.title}. {news.summary}"
    hook = plan.hook_title if plan else ""
    # The judge sees where the picture came from, and whether that page is the
    # story's own company: Mistral's key art on mistral.ai is its own artwork,
    # the same style on a news or fan site is someone's AI illustration.
    provenance = _provenance(tool_context, media_path)
    origin = str(provenance.get("origin") or "")
    url = str(provenance.get("url") or "")
    pages = [page for page in (url, str(provenance.get("context_url") or "")) if page]
    official = await _official_level(tool_context, pages, news)
    workdir = _run_workdir(tool_context)

    def look() -> tuple[Any, dict]:
        sheet = cover_vision.contact_sheet(media_path, is_video, workdir)
        return sheet, cover_vision.judge_cover_media(
            sheet,
            story,
            hook,
            provenance=_provenance_text(provenance),
            official=official,
        )

    try:
        sheet, verdict = await _within(_cap(tool_context, _JUDGE_CAP_S, 60.0), look)
    except TimeoutError:
        return {"ok": False, "error": "the picture check took too long; build from "
                "best_so_far or the best candidate", "inspections_left": left}
    except (RuntimeError, OSError) as exc:
        return {"ok": False, "error": str(exc), "inspections_left": left}

    kind = str(verdict.get("kind") or "") or cover_vision.kind_from_problems(
        verdict["problems"], verdict["verdict"]
    )
    tier = cover_vision.tier_for(kind, origin, verdict["problems"], official)
    focus = verdict["focus"] or {"x": 0.0, "y": 0.0, "w": 0.0, "h": 0.0}
    best_start = sheet.timestamps[verdict["best_frame"] - 1] if is_video else 0.0
    verdicts = dict(state.get(_K_VERDICTS) or {})
    verdicts[_media_key(media_path)] = {
        "verdict": verdict["verdict"],
        "score": verdict["score"],
        "problems": list(verdict["problems"]),
        "focus": verdict["focus"],
        "clean": verdict["clean"],
        # The subject box belongs to this moment only (see build_cover).
        "best_start_s": round(best_start, 3),
        "kind": kind,
        "tier": tier,
        "official": official,
        "origin": origin,
        "url": url,
        "path": str(media_path),
        "is_video": bool(is_video),
    }
    state[_K_VERDICTS] = verdicts
    return {
        "ok": True,
        "verdict": verdict["verdict"],
        "score": verdict["score"],
        "problems": verdict["problems"],
        "kind": kind,
        "tier": tier,
        "official": official == cover_vision.OFFICIAL_EXACT,
        "reason": verdict["reason"],
        "best_start_s": round(best_start, 2),
        "retrim_from": str(media_path) if is_video else "",
        "focus_x": round(focus["x"], 3),
        "focus_y": round(focus["y"], 3),
        "focus_w": round(focus["w"], 3),
        "focus_h": round(focus["h"], 3),
        "inspections_left": left,
        "best_so_far": _best_so_far(tool_context),
        "error": "",
    }


def _judged_unusable(tool_context: ToolContext, media_path: str) -> bool:
    """True when a tier-0 verdict covers this media: its own, one on anything
    it was cut from, or one on the same download (:func:`_judged_family`)."""
    return _tier0_verdict(tool_context, media_path) is not None


def _judged_moment(tool_context: ToolContext, media_path: str) -> Optional[tuple[str, float]]:
    """The inspected video and its judged moment, when this clip does not open on it.

    None when the clip already opens on the judged frame, when it was cut
    deliberately by retrim_clip (another moment the reviewer asked for), or
    when nothing about it was inspected. download_and_trim's clip starts
    wherever the trim chose, and an untrimmed source starts at 0: built as
    they were, the approved frame was neither the first frame nor the poster.
    """
    lineage = tool_context.state.get(_K_LINEAGE) or {}
    link = lineage.get(_media_key(media_path))
    if isinstance(link, dict) and link.get("start_s") is not None:
        return None
    verdicts = tool_context.state.get(_K_VERDICTS) or {}
    for key, offset in _lineage_chain(tool_context, media_path):
        record = verdicts.get(key)
        if not isinstance(record, dict):
            continue
        if not record.get("is_video"):
            return None
        start = float(record.get("best_start_s") or 0.0)
        source = str(record.get("path") or key)
        if offset is not None and abs(offset - start) <= _SAME_FRAME_TOLERANCE_S:
            duration = media_tools._media_duration(media_path)
            if not duration or duration <= _MAX_COVER_S + 0.5:
                return None
        return (source, start) if Path(source).exists() else None
    return None


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
    ai_illustration (a clip cut from it, the file it was cut from, or another
    download of the same URL) is refused: the cover is never AI-generated. A text graphic that scored below 3 is refused until
    find_reference_photo was tried and no real or official picture exists,
    and the plain background while any picture was inspected or downloaded
    or before find_reference_photo was tried; the error says what to build
    instead. Media from an image link in the reviewer's feedback is never
    refused. A video that does not open on the inspected moment (an
    untrimmed source, or download_and_trim's clip) is first cut there with
    retrim_clip, so the approved frame opens the cover.

    Args:
        media_path: Local path of the trimmed clip (from download_and_trim /
            retrim_clip) or the downloaded image (from download_image).
        is_video: True for a video clip, False for a still-image fallback
            (rendered as a 6 s slow-zoom video).
        source_media_url: The original URL the media came from. May be left
            empty: the URL each download came from is recorded by the tools
            and wins over this.
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
        title, highlight, used_fallback_image, drawn_background (true when
        the cover is the plain background with no picture), picture_verdict
        ('use', 'reject' or 'unchecked'; the reviewer is warned about
        anything but 'use'), source_credit, source_origin, source_media_url,
        artifact versions and warnings.
        On failure: ok (false) and error (str).
    """
    return await _build_cover(
        media_path, is_video, source_media_url, title, highlight,
        focus_x, focus_y, focus_w, focus_h, use_inspection,
        tool_context=tool_context,
    )


async def _build_cover(
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
    salvage: bool = False,
) -> dict:
    """build_cover's body. ``salvage`` (ensure_cover) skips the ladder's waiting
    rules, never the rule against AI art made by a third party."""
    # Checked whatever use_inspection says: these are sourcing rules (the
    # hard no-AI rule among them), not crop preferences.
    refusal = _cover_refusal(tool_context, media_path)
    if refusal and (not salvage or _judged_unusable(tool_context, media_path)):
        return {"ok": False, "error": refusal}
    warnings: list[str] = []
    drawn_background = _is_placeholder(media_path)
    if _rejected_by_reviewer(tool_context, media_path):
        warnings.append("the reviewer rejected this picture in an earlier round")
    if is_video and use_inspection and not drawn_background:
        moment = await asyncio.to_thread(_judged_moment, tool_context, media_path)
        if moment is not None:
            cut = await retrim_clip(moment[0], moment[1], tool_context=tool_context)
            if cut.get("ok"):
                media_path = str(cut["clip_path"])
            else:
                warnings.append(
                    f"could not cut the inspected moment out of the video: {cut.get('error')}"
                )
    built_from = str(media_path)
    provenance = {} if drawn_background else _provenance(tool_context, media_path)
    # The recorded download URL wins: the agent often passes the article's
    # URL, which says nothing about where the picture itself came from.
    final_source_url = "" if drawn_background else (
        str(provenance.get("url") or "").strip() or source_media_url.strip()
    )
    source_credit = str(provenance.get("credit") or "").strip()
    source_origin = str(provenance.get("origin") or "")
    plan = get_model(tool_context.state, K_PLAN, CarouselPlan)
    design = get_model(tool_context.state, K_DESIGN, CarouselDesign) or CarouselDesign()

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

    # What the picture check said about this media, for the QA gate and the
    # reviewer: a chart or a rejected picture never ships silently.
    built, _ = (None, None) if drawn_background else _verdict_for(tool_context, media_path)
    picture: dict[str, Any] = {"picture_verdict": ""}
    if built:
        kind, tier = _kind_and_tier(built)
        picture = {
            "picture_verdict": "use" if built.get("verdict") == "use" else "reject",
            "picture_kind": kind,
            "picture_tier": tier,
            "picture_score": int(built.get("score") or 0),
        }
    elif not drawn_background:
        picture = {"picture_verdict": "unchecked"}
    picture["picture_from_reviewer"] = not drawn_background and _from_reviewer(tool_context, media_path)
    picture["share_alike_skipped"] = int(tool_context.state.get(_K_SHARE_ALIKE_SKIPPED) or 0)

    # Validated before anything is rendered or saved. Two runs crashed here on
    # an em dash inside an image URL; a failure is now an ok:false the agent
    # can act on, and an unpublishable credit is dropped rather than the cover.
    spec: Optional[CoverSpec] = None
    spec_error: Exception | None = None
    for credit in dict.fromkeys((source_credit, "")):
        try:
            spec = CoverSpec(
                video_artifact=COVER_VIDEO_ARTIFACT,
                poster_artifact=COVER_POSTER_ARTIFACT,
                source_media_url=final_source_url,
                title=final_title,
                highlight=final_highlight,
                used_fallback_image=not is_video,
                drawn_background=drawn_background,
                source_credit=credit,
                source_origin=source_origin,
                **picture,
            )
            break
        except (ValueError, ValidationError) as exc:
            spec_error = exc
    if spec is None:
        return {"ok": False, "error": f"the cover record was rejected: {spec_error}"}
    if spec.source_credit != source_credit:
        warnings.append(f"the photo credit was dropped as unpublishable: {source_credit!r}")
    source_credit = spec.source_credit
    if drawn_background:
        warnings.append(
            "this cover is the plain drawn background with no picture: say so in "
            "your summary; the reviewer is warned"
        )
    else:
        best = _best_so_far(tool_context)
        if built and best and _media_key(best["path"]) != _media_key(media_path):
            kind, tier = _kind_and_tier(built)
            mine = {"verdict": built.get("verdict"), "tier": tier, "score": int(built.get("score") or 0)}
            if _rank(mine) < _rank(best):
                warnings.append(
                    f"best_so_far ranks above the media this cover was built from ({kind}, "
                    f"tier {tier}, score {mine['score']}): {_describe(best)}"
                )
        if spec.picture_verdict != "use" and not spec.picture_from_reviewer:
            warnings.append(
                "the picture check did not approve this picture "
                f"({spec.picture_verdict}); the reviewer is warned"
            )

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

    # Only the duration is new, and it is a number: no text rule can fail now.
    set_model(tool_context.state, K_COVER, spec.model_copy(update={"duration_s": duration_s}))
    # What this cover shows, so a reviewer's "wrong picture" in the next
    # round takes it (and what it was cut from) out of best_so_far.
    tool_context.state[_K_BUILT] = {
        "keys": [] if drawn_background else [key for key, _ in _lineage_chain(tool_context, built_from)],
    }

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
        "drawn_background": drawn_background,
        "picture_verdict": spec.picture_verdict,
        "source_credit": source_credit,
        "source_origin": source_origin,
        "source_media_url": final_source_url,
        "warnings": warnings,
        "error": "",
    }


# ---------------------------------------------------------------------------
# Salvage: a cover without the model
# ---------------------------------------------------------------------------

# Time the salvage keeps for its very last rung, the drawn background.
_SALVAGE_PLACEHOLDER_S = 45.0


async def ensure_cover(tool_context: ToolContext, *, budget_s: float = 240.0) -> dict:
    """Build a cover when the cover agent failed or ran out of time.

    The orchestrator's last line, with no model involved: the BBC run hit its
    20-minute step timeout with no cover at all. In order: best_so_far, any
    other picture downloaded in this step (images first), a Wikimedia
    reference photo of the story's subjects, and last the drawn background
    (which the QA gate and the reviewer are warned about). The ladder's
    waiting rules are skipped; AI art by a third party is still never used.

    Args:
        tool_context: A context over the pipeline's session state (the
            orchestrator builds one from its invocation context).
        budget_s: Wall-clock budget for the whole salvage.

    Returns:
        The successful build_cover result plus ``salvaged_from`` (the media
        path, or 'drawn background'), or ``{"ok": False, "error": ...}``.
    """
    state = tool_context.state
    ends = time.monotonic() + budget_s
    # The step's own deadline has passed; the tools get the salvage's.
    state[K_COVER_DEADLINE] = time.time() + budget_s
    tried: set[str] = set()
    errors: list[str] = []

    async def attempt(path: str, is_video: bool) -> Optional[dict]:
        key = _media_key(path)
        left = ends - time.monotonic() - _SALVAGE_PLACEHOLDER_S
        if key in tried or left <= 5.0:
            return None
        tried.add(key)
        try:
            built = await asyncio.wait_for(
                _build_cover(path, is_video, tool_context=tool_context, salvage=True), timeout=left,
            )
        except TimeoutError:
            errors.append(f"{Path(path).name}: took too long")
            return None
        if built.get("ok"):
            return {**built, "salvaged_from": str(path)}
        errors.append(f"{Path(path).name}: {built.get('error')}")
        return None

    best = _best_so_far(tool_context)
    if best is not None:
        done = await attempt(best["path"], bool(best["is_video"]))
        if done:
            return done
    downloaded = _downloaded_pictures(tool_context)
    downloaded.sort(key=lambda path: Path(path).suffix.lower() not in media_tools.IMAGE_EXTS)
    for path in downloaded:
        done = await attempt(path, Path(path).suffix.lower() not in media_tools.IMAGE_EXTS)
        if done:
            return done
    if ends - time.monotonic() > _SALVAGE_PLACEHOLDER_S + 30.0:
        for ref in (await _reference_lookup(tool_context, _story_subjects(tool_context)))[:2]:
            fetched = await download_image(ref["url"], tool_context=tool_context)
            if fetched.get("ok"):
                done = await attempt(fetched["path"], False)
                if done:
                    return done
    background = await create_placeholder_background(tool_context=tool_context)
    if not background.get("ok"):
        return {"ok": False, "error": f"no cover could be built: {background.get('error')}"}
    built = await _build_cover(background["path"], False, tool_context=tool_context, salvage=True)
    if built.get("ok"):
        return {**built, "salvaged_from": "drawn background"}
    errors.append(f"drawn background: {built.get('error')}")
    return {"ok": False, "error": "no cover could be built: " + "; ".join(errors)}


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

1. The cover is NEVER AI-generated by us, and it always shows a real
   picture. It is sourced from the update: the announcement/event clip
   (trimmed into the cover window), or - fallback - the update's own image
   (photo, poster, paper screenshot, product UI, blog hero) turned into a
   6 s slow-zoom cover video. Artwork, product renders and demo footage that
   the story's own company published are sourced media too
   (inspect_cover_media reports them as kind official_visual). AI art made
   by anyone else (kind ai_art, problem ai_illustration) is never used.
   When no sourced picture works, a credited, free-licensed Wikimedia
   Commons photo of the story's people, organisation or place
   (find_reference_photo) comes before any plain background. The plain
   drawn background (create_placeholder_background) is the very last
   resort, only when no picture could be downloaded at all, and the
   reviewer is warned about it.
2. Cover ONLY. Do not create, modify, or discuss body slides or the CTA slide.
3. The title comes from the plan's hook_title and the highlighted phrase from
   hook_highlight. Only override them when rework feedback explicitly asks for
   a different title. The highlight must stay a VERBATIM substring of the
   title; keep the title to 9 words or fewer, and aim for 40 characters or
   fewer so it renders large. build_cover returns a warnings list - read it;
   a hook flagged as too wide has been shrunk and will not read in a feed.
4. You MUST finish by calling build_cover successfully - that is what saves
   the cover artifacts and records the CoverSpec for the rest of the pipeline.

## Workflow - the sourcing ladder (NEVER stop before rung 7)

1. Call find_source_clip to pick the best sourced media (video preferred).
   It scans the news media_urls, the source page, every page LINKED in the
   news body text, AND runs a live trend-aware visual search for the topic.
   It ranks current prominent imagery by topic relevance, official/source
   affinity, useful visual signals and generic-asset penalties. Inspect
   trend_search and image_candidates in the result; never choose an image
   merely because it was the first available URL. You may pass search_query
   to sharpen the hunt (e.g. "<product> launch keynote demo").
   It allows at most 3 calls per step (calls_left in the result): re-call it
   only with a sharper, DIFFERENT search_query, never the same one. When
   found is false it may already return free-licensed Wikimedia reference
   photos (origin wikimedia); treat them like any other image_candidates.
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
   even when it is "the official video". Read verdict, score, kind, tier
   and best_so_far in the result:
   - kind real_photo (tier 3, or tier 2 for a Wikimedia photo): a real
     photo or footage of the subject.
   - kind official_visual (tier 2 when it came from the company's own
     site, tier 1 from anywhere else): artwork, a product render or demo
     footage the story's own company published. It is sourced, not AI art.
   - kind text_graphic (tier 1): a text card, chart, slide or web page.
     A picture flagged generic_stock is tier 1 too.
   - kind ai_art or unusable (tier 0): never used. problems containing
     "ai_illustration" means AI art made by a third party; build_cover
     refuses it, any clip cut from it, the file an AI-art clip was cut from
     and any new download of the same URL, not even as a fallback.
   - best_so_far is the strongest picture inspected so far (verdict "use"
     first, then a score of 3 or more, then tier, then score), with its
     path and is_video; for a video also retrim_from and best_start_s.
     build_cover cuts that moment out of a video by itself, so building
     best_so_far directly always opens on the approved frame.
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
   - You have at most 6 inspections per step, plus the one kept for a video
     downloaded from video_url and two kept for photos downloaded from
     find_reference_photo.
   - If inspect_cover_media fails (ok false), continue with the best
     candidate you have; the check is a help, never a blocker.
5. Decide. Build best_so_far when its verdict is "use", or when it is
   tier 3 and scored 4 or more. Otherwise call find_reference_photo: pass
   subjects in the order
   people, organisation, product, place, and prefer a subject that is not
   a portrait (the organisation, product or place) unless the story is
   about a person. Download and inspect its top 1-2 results (download_image,
   then inspect_cover_media), then build best_so_far. A rejected real photo
   or official visual still beats a text graphic, and a text graphic that
   scored 3 or more still beats the plain background. build_cover refuses a
   text graphic that scored below 3 until find_reference_photo has been
   tried and no real or official picture exists.
6. Only when nothing could be downloaded at all (neither find_source_clip
   nor find_reference_photo gave a picture that downloads): call
   create_placeholder_background and use its path as the image. build_cover
   refuses it while any picture was inspected or downloaded, and before
   find_reference_photo has been tried. The reviewer is warned about the
   plain background, and about any picture the check did not approve.
7. ALWAYS call build_cover with the local media path and is_video set
   accordingly. source_media_url may be left empty: the tools record the
   URL each download came from. Leave focus_x / focus_y / focus_w / focus_h
   at 0: build_cover applies the inspection by itself - the logo-free area
   to every clip cut from the inspected file, and the subject crop (a
   full-width band for wide group shots) to the inspected image whatever
   its verdict, or to a video clip retrimmed at best_start_s after a "use"
   verdict. Keep use_inspection true; a crop the reviewer rejects is fixed
   as described under Rework. Leave title and highlight empty
   so the plan's hook is used. When build_cover refuses the media, its
   error says what to build instead. The cover MUST be created on every
   run; no cover at all is never acceptable.
8. Finish with a one-paragraph summary: which media you used (URL and origin
   - media_urls / source_page / body_page / research_page / trend_search /
   web_search / wikimedia), sourced clip vs image, its kind, tier, verdict
   and score, the credit when it is a Wikimedia photo, what you rejected and
   why, final duration, and the artifact filenames. If the cover is the
   drawn background, say explicitly that the cover has no picture, so the
   reviewer knows no picture could be found.

## Failure handling

- Tools report failures as ok=false with an error message instead of crashing.
  Read the error, then try the next-best candidate (another video URL, then
  the next image, then find_reference_photo, then best_so_far, and only then
  the drawn background).
- The step has a time limit. Near its end find_source_clip,
  find_reference_photo and download_and_trim stop and say so, and
  build_cover no longer waits for find_reference_photo: build the cover
  then, from best_so_far or the best picture you downloaded. A cover step
  that ends without one gets a cover built automatically.
- NEVER finish without a successful build_cover call.

## Rework

When rework feedback is present, treat it as your highest-priority
instruction and rebuild the cover accordingly:

- "Cover has no picture" (QA found the drawn background) - skip
  find_source_clip. Call find_reference_photo with broader subjects (the
  story's country, city, organisation or sector), download_image and
  inspect_cover_media its top results, then build from best_so_far.
- "Cover picture was not approved by the picture check" - skip
  find_source_clip (its sources were searched already; a new search costs
  money and finds the same pages). Call find_reference_photo (the story's
  subjects, then broader ones), download_image and inspect_cover_media its
  top results, then build from best_so_far; when nothing better is found,
  rebuild from best_so_far.
- Feedback containing an image URL - call download_image on that URL,
  inspect it with inspect_cover_media, and build the cover from it.
  build_cover never refuses the reviewer's own image.
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
  rerun find_source_clip with a sharper topic + launch/demo/current query,
  or call find_reference_photo with the subject the reviewer names; never
  reuse the same merely available image (the picture the reviewer turned
  down is no longer offered as best_so_far). Inspect the new candidate
  with inspect_cover_media, then rebuild.
- A video being playable is not proof that it is relevant. Reject search hits
  whose title has no distinctive person, company, product, or event term from
  the story (for example, unrelated trending anime for a hardware story).

Always finish rework by calling build_cover again so the CoverSpec in state
and the cover artifacts are replaced with the corrected version.
"""


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def _rejects_picture(feedback: str) -> bool:
    """True when a reviewer's feedback finds fault with the cover's picture.

    Any link counts (it is the picture they want instead). Otherwise one
    clause has to name the picture and a fault, with no praise or keep that
    is not negated ("not good" is a fault), and no crop or moment of the
    clip, which is fixed on the same picture.
    """
    if _URL_RE.search(feedback or ""):
        return True
    for clause in _CLAUSE_RE.split(feedback or ""):
        if not (_PICTURE_WORD_RE.search(clause) and _PICTURE_FAULT_RE.search(clause)):
            continue
        kept = False
        for match in _PICTURE_KEPT_RE.finditer(clause):
            before = clause[:match.start()].lower().split()[-2:]
            if not any(w in ("not", "no", "never") or w.endswith(("n't", "n’t")) for w in before):
                kept = True
                break
        if not kept:
            return True
    return False


def _reset_step_state(callback_context: CallbackContext) -> Optional[types.Content]:
    """``before_agent_callback``: start every cover step with fresh budgets.

    ADK 2.7.0 keeps ``temp:`` state across the child agents of one invocation,
    so without this a QA re-run of the cover would start with its searches
    and reference lookups spent, and with the reference rung marked as tried.
    Only keys holding something are written, so a first step adds no event.

    The vision budget (``_K_INSPECTIONS``) and the held-video look are NOT
    reset: they cap the billed vision calls of a whole invocation, and a QA
    re-run still gets its two reference-photo looks.

    On a human rejection that finds fault with the picture
    (:func:`_rejects_picture`), the picture the last cover was built from
    leaves best_so_far: it stayed the pick and was rebuilt round after round.

    Returns:
        Always ``None`` - the agent proceeds normally.
    """
    state = callback_context.state
    for key, empty in _STEP_STATE.items():
        if state.get(key):
            state[key] = empty
    verdict = state.get(K_VERDICT)
    built = state.get(_K_BUILT)
    if (
        isinstance(verdict, dict)
        and verdict.get("status") == "rejected"
        and isinstance(built, dict)
        and built.get("keys")
        and _rejects_picture(
            f"{verdict.get('feedback') or ''}\n{state.get(K_REWORK_FEEDBACK) or ''}"
        )
    ):
        rejected = list(state.get(_K_REVIEWER_REJECTED) or [])
        added = [key for key in built["keys"] if key not in rejected]
        if added:
            state[_K_REVIEWER_REJECTED] = rejected + added
    return None


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
            "clip, page-scraped or web-searched media, image fallback, a "
            "credited Wikimedia photo of the story's subject, or a drawn "
            "background as the flagged last resort - composited with the "
            "cover overlay and the plan's hook title."
        ),
        instruction=instruction,
        tools=[
            FunctionTool(find_source_clip),
            FunctionTool(find_reference_photo),
            FunctionTool(download_and_trim),
            FunctionTool(download_image),
            FunctionTool(create_placeholder_background),
            FunctionTool(retrim_clip),
            FunctionTool(inspect_cover_media),
            FunctionTool(build_cover),
        ],
        before_agent_callback=_reset_step_state,
        # Orchestrator-driven pipeline node: never LLM-transfer elsewhere.
        # (Both flags True + no sub_agents selects SingleFlow, so
        # transfer_to_agent is never offered to the model.)
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )
