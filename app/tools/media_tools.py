"""Media tools for the First-Page Visual agent.

Three jobs (see docs/CONTRACTS.md file map):

1. ``find_source_clip(news)``   - rank the best sourced video (preferred) or
   image URL from a news item's ``media_urls``, its source page, every page
   linked in its body text, plus a live trend-aware visual search. Current,
   topical, source-affine imagery outranks generic merely available assets.
   ``placeholder_background`` guarantees a cover can ALWAYS be built.
2. ``download_and_trim(url)``   - fetch the clip via the yt-dlp Python API and
   trim it into the configured cover window (settings.cover_clip_min_s..max_s,
   default 4-15 s), silent H.264 mp4.
3. ``compose_cover(...)``       - scale/center-crop the media to 1080x1350,
   composite the STRANGE-COVER overlay template plus a Pillow-rendered title
   block (warm-white, condensed extra-bold uppercase, solid green highlight
   phrase) and produce the final cover mp4 + first-frame poster PNG.

The cover is NEVER AI-generated (skills/cover-style.md): a sourced clip, or -
fallback - the update's own image turned into a 6 s slow-zoom video.

All ffmpeg work shells out to ``settings.ffmpeg_bin`` with explicit timeouts.
All intermediate files live under a caller-provided run-specific ``workdir``
(default: ``settings.workdir / "adhoc"``).  These functions are plain,
type-hinted callables so the agent layer can wrap them in ADK ``FunctionTool``s.
"""

from __future__ import annotations

import html
import json
import math
import re
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

import requests
from PIL import Image, ImageDraw, ImageFont
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, download_range_func

from app.config import settings
from app.text_rules import require_no_em_dash
from app.tools.brand_layout import (
    ACCENT_GREEN,
    HEADLINE_FONT_SIZE,
    HEADLINE_MAX_LINES,
    HEADLINE_MIN_FONT_SIZE,
    WARM_WHITE,
    draw_slide_number,
    headline_font,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".gifv"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
VIDEO_HOSTS = (
    "youtube.com",
    "youtu.be",
    "vimeo.com",
    "streamable.com",
    "twitter.com",
    "x.com",
    "tiktok.com",
    "twitch.tv",
    "dailymotion.com",
)

_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
}
# (connect, read) timeouts for every requests call - never hang the pipeline.
_PAGE_TIMEOUT = (10, 30)
_IMAGE_TIMEOUT = (10, 60)
_FFMPEG_TIMEOUT_S = 300
_FFPROBE_TIMEOUT_S = 60
_MAX_PROBES = 4  # cap yt-dlp probes per find_source_clip call
_TREND_PAGE_LIMIT = 4
_IMAGE_CANDIDATE_LIMIT = 5

# Title styling (current baskaranbuilds.com tokens; skills/cover-style.md).
_TEXT_PRIMARY = (232, 228, 214, 255)  # #E8E4D6
_ACCENT_GREEN = (*ACCENT_GREEN, 255)  # #B8EF43
_TITLE_MAX_LINES = HEADLINE_MAX_LINES
_TITLE_MAX_WIDTH_FRAC = 0.63  # inner span between the template's arrow glyphs
_TITLE_CENTER_Y_FRAC = 0.79  # matches the template's own title-block center

# Region of the template occupied by its baked-in EXAMPLE title text
# ("STOP PROMPTING YOUR AI, GIVE IT A LOOP") - measured on the shipped
# STRANGE-COVER (1).png. It is scrubbed before compositing the real title.
# Fractions of width/height; excludes the side arrow glyphs (0.093-0.167 and
# 0.839-0.907) and the grid floor.
_TEMPLATE_TEXT_BOX = (0.175, 0.705, 0.83, 0.872)  # (x0, y0, x1, y1)
_STILL_COVER_SECONDS = 6.0
_COVER_FPS = 30
_MIN_COVER_IMAGE_PIXELS = 450_000
_MIN_COVER_IMAGE_SIDE = 360
_MAX_COVER_IMAGE_ASPECT = 3.2

_TOPIC_STOPWORDS = {
    "about", "after", "again", "from", "into", "just", "latest", "more",
    "new", "over", "that", "the", "their", "this", "with", "your",
}
_GOOD_VISUAL_TOKENS = {
    "announcement", "cover", "demo", "event", "hero", "keynote", "launch",
    "product", "release", "screenshot", "stage", "visual",
}
_BAD_VISUAL_TOKENS = {
    "avatar", "badge", "favicon", "icon", "logo", "placeholder", "sprite",
    "tracking", "transparent",
}


@dataclass(frozen=True)
class _MediaCandidate:
    url: str
    kind: str
    score: int
    origin: str
    context_url: str = ""
    reason: str = ""


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------


def _ensure_workdir(workdir: str, subdir: str) -> Path:
    """Resolve (and create) the working folder for intermediate files.

    Args:
        workdir: Run-specific folder passed by the caller; empty string falls
            back to ``settings.workdir / "adhoc"``.
        subdir: Purpose-specific subfolder ("clips", "cover", ...).

    Returns:
        The created directory as a ``Path``.
    """
    base = Path(workdir) if str(workdir).strip() else (settings.workdir / "adhoc")
    target = base / subdir
    target.mkdir(parents=True, exist_ok=True)
    return target


def _ffprobe_bin() -> str:
    """Derive the ffprobe executable from ``settings.ffmpeg_bin``."""
    p = Path(settings.ffmpeg_bin)
    if len(p.parts) > 1 and "ffmpeg" in p.name.lower():
        sibling = p.with_name(p.name.lower().replace("ffmpeg", "ffprobe"))
        if sibling.exists():
            return str(sibling)
    return "ffprobe"


def _run_ffmpeg(args: list[str], timeout_s: int = _FFMPEG_TIMEOUT_S) -> None:
    """Run an ffmpeg command, raising ``RuntimeError`` with the stderr tail."""
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout_s)
    if proc.returncode != 0:
        tail = (proc.stderr or "")[-2000:]
        raise RuntimeError(f"ffmpeg failed (exit {proc.returncode}): {tail}")


def _media_duration(path: str | Path) -> float:
    """Return media duration in seconds (0.0 when it cannot be determined)."""
    try:
        proc = subprocess.run(
            [
                _ffprobe_bin(),
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=_FFPROBE_TIMEOUT_S,
        )
        if proc.returncode == 0:
            data = json.loads(proc.stdout or "{}")
            dur = float(data.get("format", {}).get("duration", 0.0))
            if dur > 0:
                return dur
    except (OSError, subprocess.TimeoutExpired, ValueError, json.JSONDecodeError):
        pass
    # Fallback: parse `ffmpeg -i` banner output.
    try:
        proc = subprocess.run(
            [settings.ffmpeg_bin, "-hide_banner", "-i", str(path)],
            capture_output=True,
            text=True,
            timeout=_FFPROBE_TIMEOUT_S,
        )
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", proc.stderr or "")
        if match:
            h, m, s = match.groups()
            return int(h) * 3600 + int(m) * 60 + float(s)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    return 0.0


def _url_ext(url: str) -> str:
    """Lower-cased file extension of a URL path ('' when none)."""
    return Path(urlparse(url).path).suffix.lower()


def _classify_url(url: str) -> str:
    """Classify a URL as 'video', 'image' or 'unknown' without network I/O."""
    ext = _url_ext(url)
    if ext in VIDEO_EXTS:
        return "video"
    if ext in IMAGE_EXTS:
        return "image"
    host = (urlparse(url).netloc or "").lower()
    if any(host == h or host.endswith("." + h) for h in VIDEO_HOSTS):
        return "video"
    return "unknown"


def _probe_with_ytdlp(url: str) -> Optional[dict[str, Any]]:
    """Probe a URL with the yt-dlp Python API without downloading.

    Returns a small dict ``{"duration": float, "title": str}`` when the URL
    resolves to playable video, ``None`` otherwise.
    """
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "playlist_items": "1",
        "socket_timeout": 20,
        "retries": 1,
        "skip_download": True,
        "http_headers": dict(_HTTP_HEADERS),
    }
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception:  # noqa: BLE001 - extractor errors vary widely (DownloadError etc.)
        return None
    if not info:
        return None
    if info.get("_type") == "playlist":
        entries = info.get("entries") or []
        if not entries:
            return None
        info = entries[0] or {}
    has_video = bool(info.get("formats") or info.get("url") or info.get("ext"))
    if not has_video:
        return None
    return {
        "duration": float(info.get("duration") or 0.0),
        "title": str(info.get("title") or ""),
    }


# ---------------------------------------------------------------------------
# 1) find_source_clip
# ---------------------------------------------------------------------------

_META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.I)
_META_KEY_RE = re.compile(r"""(?:property|name)\s*=\s*["']([^"']+)["']""", re.I)
_META_CONTENT_RE = re.compile(r"""content\s*=\s*["']([^"']+)["']""", re.I)
_VIDEO_SRC_RE = re.compile(
    r"""<(?:video|source)\b[^>]*\bsrc\s*=\s*["']([^"']+)["']""", re.I
)
_IFRAME_SRC_RE = re.compile(r"""<iframe\b[^>]*\bsrc\s*=\s*["']([^"']+)["']""", re.I)
_HREF_MEDIA_RE = re.compile(
    r"""href\s*=\s*["']([^"']+\.(?:mp4|mov|webm|m4v))(?:\?[^"']*)?["']""", re.I
)

# meta key -> (kind, score)
_META_SCORES: dict[str, tuple[str, int]] = {
    "og:video": ("video", 90),
    "og:video:url": ("video", 90),
    "og:video:secure_url": ("video", 90),
    "twitter:player:stream": ("video", 85),
    "og:image": ("image", 45),
    "og:image:url": ("image", 45),
    "twitter:image": ("image", 42),
    "twitter:image:src": ("image", 42),
}


def _scrape_page_media(page_url: str) -> list[tuple[str, str, int]]:
    """Scrape a source page for media candidates.

    Args:
        page_url: The news item's source page URL.

    Returns:
        List of ``(url, kind, score)`` tuples; kind is 'video'/'image'/'unknown'.
        Network failures return an empty list - scraping is best-effort.
    """
    try:
        resp = requests.get(page_url, headers=_HTTP_HEADERS, timeout=_PAGE_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException:
        return []
    ctype = (resp.headers.get("Content-Type") or "").lower()
    if "html" not in ctype and "xml" not in ctype:
        return []
    text = resp.text[:3_000_000]

    found: list[tuple[str, str, int]] = []

    def add(raw: str, kind: str, score: int) -> None:
        url = urljoin(page_url, html.unescape(raw.strip()))
        if url.startswith(("http://", "https://")):
            found.append((url, kind, score))

    for tag in _META_TAG_RE.findall(text):
        key_m = _META_KEY_RE.search(tag)
        content_m = _META_CONTENT_RE.search(tag)
        if not key_m or not content_m:
            continue
        entry = _META_SCORES.get(key_m.group(1).strip().lower())
        if entry:
            add(content_m.group(1), entry[0], entry[1])

    for raw in _VIDEO_SRC_RE.findall(text):
        add(raw, "video", 88)
    for raw in _IFRAME_SRC_RE.findall(text):
        if _classify_url(urljoin(page_url, raw)) == "video":
            add(raw, "video", 80)
    for raw in _HREF_MEDIA_RE.findall(text):
        add(raw, "video", 82)
    return found


_URL_IN_TEXT_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.I)
_BODY_URL_SCRAPE_LIMIT = 4  # pages linked in the body text scraped per call


def _site(url: str) -> str:
    """Return a comparable hostname for source-affinity scoring."""
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _topic_tokens(news: dict) -> set[str]:
    """Extract useful topic words for lightweight visual relevance scoring."""
    text = " ".join(
        [str(news.get("title") or ""), *[str(tag) for tag in news.get("tags") or []]]
    ).lower()
    return {
        token
        for token in re.findall(r"[a-z0-9]+", text)
        if len(token) >= 3 and token not in _TOPIC_STOPWORDS
    }


def _rank_candidate(
    candidate: _MediaCandidate,
    news: dict,
    source_url: str,
) -> _MediaCandidate:
    """Score topicality, freshness signals, source affinity, and visual quality."""
    score = candidate.score
    reasons = [candidate.reason] if candidate.reason else []
    context = f"{candidate.url} {candidate.context_url}".lower()
    overlap = sorted(token for token in _topic_tokens(news) if token in context)
    if overlap:
        bonus = min(len(overlap) * 3, 12)
        score += bonus
        reasons.append(f"topic match +{bonus}")

    source_site = _site(source_url)
    context_site = _site(candidate.context_url or candidate.url)
    if source_site and context_site == source_site:
        score += 12
        reasons.append("official/source-site +12")

    good = sorted(token for token in _GOOD_VISUAL_TOKENS if token in context)
    if good:
        bonus = min(len(good) * 2, 8)
        score += bonus
        reasons.append(f"visual signal +{bonus}")
    bad = sorted(token for token in _BAD_VISUAL_TOKENS if token in context)
    if bad:
        penalty = min(len(bad) * 12, 36)
        score -= penalty
        reasons.append(f"generic asset -{penalty}")

    return _MediaCandidate(
        url=candidate.url,
        kind=candidate.kind,
        score=score,
        origin=candidate.origin,
        context_url=candidate.context_url,
        reason="; ".join(reasons),
    )


def _search_trending_pages(
    news: dict,
    search_query: str = "",
) -> tuple[list[str], str]:
    """Find current pages carrying prominent, source-grounded topic visuals.

    This deliberately searches beyond URLs already attached to the news item.
    The returned pages are scraped for their own og:image/og:video assets and
    ranked with a freshness/relevance bonus by :func:`find_source_clip`.
    """
    topic = re.sub(
        r"\s+",
        " ",
        (search_query or str(news.get("title") or "")).strip(),
    )
    if not topic:
        return [], "trend search skipped: empty topic"
    published = str(news.get("published_at") or "").strip()
    date_hint = published[:10] if published else str(datetime.now(timezone.utc).date())
    query = (
        f'As of {date_hint}, find the newest prominent visual coverage for "{topic}". '
        "Prefer an official launch image, product demo screenshot, keynote still, "
        "or current reputable news image. Return pages that visibly carry the "
        "relevant image. Avoid generic stock art, logos, icons, and old unrelated media."
    )
    try:
        # Local import keeps the media layer usable in minimal/offline contexts.
        from app.tools.research_tools import search_web

        result = search_web(query)
    except Exception as exc:  # noqa: BLE001 - trend search is best-effort
        return [], f"trend search unavailable: {exc}"
    if result.get("status") != "ok":
        return [], f"trend search unavailable: {result.get('message', 'unknown error')}"
    pages = [
        str(url).strip()
        for url in result.get("sources") or []
        if str(url).strip().startswith(("http://", "https://"))
    ]
    return pages[:_TREND_PAGE_LIMIT], f"live trend search checked {len(pages[:_TREND_PAGE_LIMIT])} page(s)"


def _search_video_online(query: str) -> Optional[dict[str, Any]]:
    """Web-search (YouTube via yt-dlp ``ytsearch``) for a playable event clip.

    Args:
        query: Free-text search, e.g. "Niu Lai movie official trailer".

    Returns:
        ``{"url", "duration", "title"}`` for the first playable result, or
        ``None`` when the search fails or returns nothing.
    """
    query = re.sub(r"\s+", " ", (query or "")).strip()
    if not query:
        return None
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": 20,
        "retries": 1,
        "skip_download": True,
        "http_headers": dict(_HTTP_HEADERS),
    }
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch3:{query}", download=False)
    except Exception:  # noqa: BLE001 - extractor errors vary widely
        return None
    for entry in (info or {}).get("entries") or []:
        if not entry:
            continue
        url = str(entry.get("webpage_url") or entry.get("url") or "").strip()
        if url:
            return {
                "url": url,
                "duration": float(entry.get("duration") or 0.0),
                "title": str(entry.get("title") or ""),
            }
    return None


def find_source_clip(news: dict, search_query: str = "") -> dict:
    """Pick the best sourced video (preferred) or image URL for the cover.

    Candidates come from the news item's ``media_urls`` list, the
    ``source_url`` itself (it may be a YouTube/Vimeo watch page), media
    scraped off the source page (og:video / og:image / <video> / iframes),
    AND every page linked inside the item's body/summary text (each scraped
    the same way - newsletter blurbs usually carry the links inline). When no
    sourced video is playable, a bounded web search (``ytsearch``) hunts for
    an event/announcement clip before falling back to the best image.

    Args:
        news: A ``NewsItem``-shaped dict (keys: ``media_urls``, ``source_url``,
            ``title``, ``body``, ...).
        search_query: Optional web-search override for the video hunt; empty
            uses the news title.

    Returns:
        Dict with keys: ``found`` (bool), ``url`` (str), ``is_video`` (bool),
        ``duration_s`` (float, 0.0 when unknown), ``origin``
        ('media_urls' | 'source_url' | 'source_page' | 'body_url' |
        'body_page' | 'trend_search' | 'web_search' | ''),
        ``image_candidates`` (ranked still alternatives), ``trend_search``
        (live-search status), and ``note`` (str).
    """
    candidate_map: dict[str, _MediaCandidate] = {}

    def add(
        url: str,
        kind: str,
        score: int,
        origin: str,
        context_url: str = "",
        reason: str = "",
    ) -> None:
        key = url.split("#", 1)[0]
        if not key:
            return
        candidate = _MediaCandidate(url, kind, score, origin, context_url, reason)
        previous = candidate_map.get(key)
        if previous is None or candidate.score > previous.score:
            candidate_map[key] = candidate

    for url in news.get("media_urls") or []:
        if not isinstance(url, str) or not url.strip():
            continue
        url = url.strip()
        kind = _classify_url(url)
        score = {"video": 100, "image": 50}.get(kind, 25)
        add(url, kind, score, "media_urls", reason="attached source media")

    source_url = str(news.get("source_url") or "").strip()
    if source_url and _classify_url(source_url) == "video":
        add(source_url, "video", 95, "source_url", source_url, "official source video")
    if source_url:
        for url, kind, score in _scrape_page_media(source_url):
            add(url, kind, score, "source_page", source_url, "official source page")

    # Pages linked inside the body/summary text (ad-hoc runs put the links
    # there): direct media URLs become candidates, other pages are scraped
    # for og:image / og:video just like the source page.
    body_text = " ".join(
        str(news.get(k) or "") for k in ("title", "summary", "body")
    )
    scraped_pages = 0
    for raw in _URL_IN_TEXT_RE.findall(body_text):
        url = raw.rstrip(".,;:!?")
        if url.split("#", 1)[0] in candidate_map or url == source_url:
            continue
        kind = _classify_url(url)
        if kind == "video":
            add(url, "video", 92, "body_url", reason="linked from news body")
        elif kind == "image":
            add(url, "image", 48, "body_url", reason="linked from news body")
        elif scraped_pages < _BODY_URL_SCRAPE_LIMIT:
            scraped_pages += 1
            for media_url, media_kind, score in _scrape_page_media(url):
                add(
                    media_url,
                    media_kind,
                    max(score - 2, 1),
                    "body_page",
                    url,
                    "page linked from news body",
                )

    # Do not settle for the first attached/available image. Search the live web
    # for the topic's current prominent visual coverage, scrape those pages,
    # then rank the resulting assets alongside the source-owned candidates.
    trend_pages, trend_note = _search_trending_pages(news, search_query)
    for rank, page_url in enumerate(trend_pages, start=1):
        page_kind = _classify_url(page_url)
        if page_kind in {"image", "video"}:
            direct_score = (58 if page_kind == "image" else 93) - rank * 2
            add(
                page_url,
                page_kind,
                direct_score,
                "trend_search",
                page_url,
                f"direct live trend result rank {rank}",
            )
            continue
        for media_url, media_kind, scraped_score in _scrape_page_media(page_url):
            if media_kind == "video":
                score = max(scraped_score, 93 - rank * 2)
            elif media_kind == "image":
                score = max(scraped_score, 58 - rank * 2)
            else:
                score = scraped_score
            add(
                media_url,
                media_kind,
                score,
                "trend_search",
                page_url,
                f"live trend result rank {rank}",
            )

    candidates = [
        _rank_candidate(candidate, news, source_url)
        for candidate in candidate_map.values()
    ]
    candidates.sort(key=lambda candidate: candidate.score, reverse=True)

    # Best image candidate - reported alongside every result so the caller
    # can drop to the image path the moment video downloads fail, without
    # re-searching.
    ranked_images = [candidate for candidate in candidates if candidate.kind == "image"]
    image_candidates = [
        {
            "url": candidate.url,
            "origin": candidate.origin,
            "score": candidate.score,
            "reason": candidate.reason,
            "context_url": candidate.context_url,
        }
        for candidate in ranked_images[:_IMAGE_CANDIDATE_LIMIT]
    ]
    image_url = image_candidates[0]["url"] if image_candidates else ""
    image_origin = image_candidates[0]["origin"] if image_candidates else ""

    def result(found: bool, url: str, is_video: bool, duration: float,
               origin: str, note: str) -> dict:
        return {
            "found": found,
            "url": url,
            "is_video": is_video,
            "duration_s": duration,
            "origin": origin,
            "image_url": image_url,
            "image_origin": image_origin,
            "image_candidates": image_candidates,
            "trend_search": trend_note,
            "note": note,
        }

    # Confirm video candidates (bounded number of network probes).
    probes = 0
    for candidate in candidates:
        if candidate.kind not in ("video", "unknown"):
            continue
        if probes >= _MAX_PROBES:
            break
        probes += 1
        info = _probe_with_ytdlp(candidate.url)
        if info is not None:
            return result(
                True,
                candidate.url,
                True,
                round(info["duration"], 2),
                candidate.origin,
                info["title"] or "probed with yt-dlp",
            )
        if _url_ext(candidate.url) in VIDEO_EXTS:
            # Direct video file that yt-dlp could not probe (e.g. signed CDN
            # URL) - trust the extension and let download_and_trim try.
            return result(
                True, candidate.url, True, 0.0, candidate.origin,
                "direct video file (probe skipped/failed)",
            )

    # No sourced video played - hunt the web for an event/announcement clip
    # (the original spec: "a small video piece on that event from web").
    query = search_query.strip() or str(news.get("title") or "").strip()
    searched = _search_video_online(query)
    if searched is not None:
        return result(
            True, searched["url"], True, round(searched["duration"], 2),
            "web_search", f"web search hit: {searched['title'] or query}",
        )

    if image_url:
        return result(
            True, image_url, False, 0.0, image_origin,
            "no playable video found; image fallback",
        )

    return result(
        False, "", False, 0.0, "",
        "no video or image candidates found (media_urls, source page, "
        "body links and web search all came up empty) - use "
        "placeholder_background for a text-only cover",
    )


# ---------------------------------------------------------------------------
# 2) download_and_trim  (+ download_image helper for the fallback path)
# ---------------------------------------------------------------------------


def download_and_trim(
    url: str, max_s: Optional[int] = None, min_s: Optional[int] = None, workdir: str = ""
) -> str:
    """Download a video via the yt-dlp Python API and trim it to the cover window.

    Long videos are section-downloaded (first ~4x``max_s`` seconds) to avoid
    pulling whole streams. The trim re-encodes to silent H.264 mp4 with
    ``+faststart``. Clips shorter than ``min_s`` are looped up to ``min_s``.

    Args:
        url: Video URL (direct file or any yt-dlp-supported page).
        max_s: Maximum clip length in seconds (default:
            ``settings.cover_clip_max_s``).
        min_s: Minimum clip length in seconds (default:
            ``settings.cover_clip_min_s``).
        workdir: Run-specific working folder (see ``_ensure_workdir``).

    Returns:
        Absolute path (str) of the trimmed mp4.

    Raises:
        RuntimeError: When the download or the ffmpeg trim fails.
    """
    max_s = int(max_s if max_s else settings.cover_clip_max_s)
    min_s = int(min_s if min_s else settings.cover_clip_min_s)
    wd = _ensure_workdir(workdir, "clips")
    stem = uuid.uuid4().hex[:12]

    pre = _probe_with_ytdlp(url)
    est_duration = float((pre or {}).get("duration") or 0.0)

    ydl_opts: dict[str, Any] = {
        "outtmpl": str(wd / f"src-{stem}.%(ext)s"),
        "format": (
            "bestvideo[height<=1080][ext=mp4]/bestvideo[height<=1080]"
            "/best[height<=1080]/best"
        ),
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "playlist_items": "1",
        "socket_timeout": 30,
        "retries": 2,
        "overwrites": True,
        "http_headers": dict(_HTTP_HEADERS),
    }
    ffmpeg_dir = Path(settings.ffmpeg_bin).parent
    if len(Path(settings.ffmpeg_bin).parts) > 1 and ffmpeg_dir.exists():
        ydl_opts["ffmpeg_location"] = str(ffmpeg_dir)
    if est_duration > 120:
        # Only fetch the head of long videos; keyframe cuts keep it seekable.
        ydl_opts["download_ranges"] = download_range_func([], [(0.0, float(max_s) * 4)])
        ydl_opts["force_keyframes_at_cuts"] = True

    src_path: Optional[str] = None
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
        if info and info.get("_type") == "playlist":
            entries = info.get("entries") or [{}]
            info = entries[0] or {}
        for req in (info or {}).get("requested_downloads") or []:
            if req.get("filepath"):
                src_path = req["filepath"]
                break
        if not src_path:
            matches = sorted(wd.glob(f"src-{stem}.*"))
            if matches:
                src_path = str(matches[0])
    except DownloadError as exc:
        # Some CDNs 403 yt-dlp's client; for plain video files fall back to a
        # direct streamed download with a browser User-Agent.
        if _url_ext(url) not in VIDEO_EXTS:
            raise RuntimeError(f"yt-dlp could not download {url}: {exc}") from exc
        direct = wd / f"src-{stem}{_url_ext(url)}"
        try:
            with requests.get(
                url, headers=_HTTP_HEADERS, timeout=_IMAGE_TIMEOUT, stream=True
            ) as resp:
                resp.raise_for_status()
                with direct.open("wb") as fh:
                    for chunk in resp.iter_content(chunk_size=1 << 20):
                        fh.write(chunk)
        except requests.RequestException as exc2:
            raise RuntimeError(
                f"could not download {url} (yt-dlp: {exc}; direct: {exc2})"
            ) from exc2
        src_path = str(direct)
    if not src_path or not Path(src_path).exists():
        raise RuntimeError(f"download reported success but no file found for {url}")

    duration = _media_duration(src_path) or est_duration
    out_path = wd / f"clip-{stem}.mp4"

    cmd: list[str] = [settings.ffmpeg_bin, "-y"]
    if 0.0 < duration < float(min_s):
        loops = max(0, math.ceil(float(min_s) / duration) - 1)
        cmd += ["-stream_loop", str(loops), "-i", src_path, "-t", f"{float(min_s):.3f}"]
    else:
        clip_len = min(float(max_s), duration) if duration > 0 else float(max_s)
        clip_len = max(clip_len, float(min_s)) if duration >= float(min_s) else clip_len
        start = 0.0
        if duration > float(max_s):
            # Skip a touch of intro, but never run past the end.
            start = min(duration * 0.08, duration - clip_len)
        cmd += ["-ss", f"{start:.3f}", "-i", src_path, "-t", f"{clip_len:.3f}"]
    cmd += [
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
    _run_ffmpeg(cmd)
    return str(out_path)


def placeholder_background(workdir: str = "") -> str:
    """Build the deterministic dark 1080x1350 fallback background still.

    Used when NO sourced media exists at all: a subtle top-to-bottom dark
    gradient the STRANGE-COVER overlay + title composite onto, so a cover is
    ALWAYS produced. Pure Pillow - this is a drawn background, not AI-generated
    imagery, so it does not violate the sourced-cover rule.

    Args:
        workdir: Run-specific folder (empty falls back to workdir/adhoc).

    Returns:
        Path of the written PNG as a string.
    """
    out_dir = _ensure_workdir(workdir, "cover")
    out_path = out_dir / "placeholder-bg.png"
    width, height = 1080, 1350
    top, bottom = 30, 6  # near-black gradient, slightly lighter at the top
    img = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(img)
    for y in range(height):
        shade = int(top + (bottom - top) * (y / max(height - 1, 1)))
        draw.line([(0, y), (width, y)], fill=(shade, shade, shade))
    img.save(out_path, format="PNG")
    return str(out_path)


def download_image(url: str, workdir: str = "") -> str:
    """Download a still image (the cover fallback source) and validate it.

    Args:
        url: Direct image URL.
        workdir: Run-specific working folder.

    Returns:
        Absolute path (str) of the downloaded image file.

    Raises:
        RuntimeError: On HTTP failure or when the payload is not a readable image.
    """
    wd = _ensure_workdir(workdir, "images")
    try:
        resp = requests.get(url, headers=_HTTP_HEADERS, timeout=_IMAGE_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"image download failed for {url}: {exc}") from exc

    ext = _url_ext(url)
    if ext not in IMAGE_EXTS:
        ctype = (resp.headers.get("Content-Type") or "").lower()
        ext = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
            "image/gif": ".gif",
        }.get(ctype.split(";", 1)[0].strip(), ".jpg")
    path = wd / f"img-{uuid.uuid4().hex[:12]}{ext}"
    path.write_bytes(resp.content)
    try:
        with Image.open(path) as img:
            img.load()
            width, height = img.size
    except OSError as exc:
        path.unlink(missing_ok=True)
        raise RuntimeError(f"downloaded file is not a readable image: {url}") from exc
    short_side = min(width, height)
    aspect = max(width / max(height, 1), height / max(width, 1))
    if (
        width * height < _MIN_COVER_IMAGE_PIXELS
        or short_side < _MIN_COVER_IMAGE_SIDE
        or aspect > _MAX_COVER_IMAGE_ASPECT
    ):
        path.unlink(missing_ok=True)
        raise RuntimeError(
            "image is unsuitable for a 1080x1350 cover "
            f"({width}x{height}, aspect {aspect:.2f}); try the next ranked "
            "image_candidates entry"
        )
    return str(path)


# ---------------------------------------------------------------------------
# 3) compose_cover - title rendering + overlay + ffmpeg render
# ---------------------------------------------------------------------------


def _load_title_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load the exact shared headline face used by the carousel system."""
    return headline_font(size)


def _line_width(
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    text: str,
) -> float:
    """Width of ``text`` measured char-by-char (matches per-char drawing)."""
    return sum(font.getlength(ch) for ch in text)


def _wrap_title(
    title: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_w: float,
) -> list[str]:
    """Wrap a cover hook into the fewest balanced shared-headline lines."""
    if _line_width(font, title) <= max_w:
        return [title]
    words = title.split(" ")
    if len(words) < 2:
        return [title]
    fallback: tuple[float, list[str]] = (float("inf"), [title])
    max_lines = min(_TITLE_MAX_LINES, len(words))
    for line_count in range(2, max_lines + 1):
        best: tuple[float, list[str]] = (float("inf"), [title])
        for cuts in combinations(range(1, len(words)), line_count - 1):
            boundaries = (0, *cuts, len(words))
            lines = [
                " ".join(words[boundaries[i] : boundaries[i + 1]])
                for i in range(line_count)
            ]
            widths = [_line_width(font, line) for line in lines]
            score = max(widths) + (max(widths) - min(widths)) * 0.12
            if score < best[0]:
                best = (score, lines)
        fallback = best
        if all(_line_width(font, line) <= max_w for line in best[1]):
            return best[1]
    return fallback[1]


def _highlight_color() -> tuple[int, int, int, int]:
    """Return the one fixed brand green for every highlight character."""
    return _ACCENT_GREEN


def _render_title_block(title: str, highlight: str) -> Image.Image:
    """Render the cover title onto a transparent 1080x1350 RGBA image.

    Centered in the lower third, up to three lines, shared 76 px-equivalent
    condensed bold grotesk typography,
    uppercase, white, with the highlight phrase in a per-character horizontal
    single brand green (#B8EF43), with no gradient or shade variation.
    """
    width, height = settings.slide_width, settings.slide_height
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    text = re.sub(r"\s+", " ", str(title or "")).strip().upper()
    if not text:
        return canvas
    hl = re.sub(r"\s+", " ", str(highlight or "")).strip().upper()
    hl_start = text.find(hl) if hl else -1
    hl_end = hl_start + len(hl) if hl_start >= 0 else -1

    max_w = width * _TITLE_MAX_WIDTH_FRAC
    font = _load_title_font(HEADLINE_FONT_SIZE)
    lines = [text]
    for size in range(HEADLINE_FONT_SIZE, HEADLINE_MIN_FONT_SIZE - 1, -2):
        font = _load_title_font(size)
        lines = _wrap_title(text, font, max_w)
        if len(lines) <= _TITLE_MAX_LINES and all(
            _line_width(font, ln) <= max_w for ln in lines
        ):
            break

    draw = ImageDraw.Draw(canvas)
    ascent, descent = font.getmetrics()
    line_h = ascent + descent
    gap = int(line_h * 0.10)
    total_h = len(lines) * line_h + (len(lines) - 1) * gap
    top = int(height * _TITLE_CENTER_Y_FRAC - total_h / 2)
    top = min(top, height - int(height * 0.06) - total_h)  # keep off the grid floor

    global_idx = 0  # char index into `text` (lines re-join with single spaces)
    for line_no, line in enumerate(lines):
        y = top + line_no * (line_h + gap)
        x = (width - _line_width(font, line)) / 2
        for ch in line:
            if hl_start <= global_idx < hl_end:
                color = _highlight_color()
            else:
                color = _TEXT_PRIMARY
            draw.text((x, y), ch, font=font, fill=color)
            x += font.getlength(ch)
            global_idx += 1
        global_idx += 1  # the space (or line break) between joined lines
    return canvas


_SCRUBBED_TEMPLATE_CACHE: dict[tuple[str, float], Image.Image] = {}


def _scrub_template_text(tpl: Image.Image) -> Image.Image:
    """Remove the template's baked-in example title text, in place.

    The shipped STRANGE-COVER template contains its reference title
    ("STOP PROMPTING YOUR AI, GIVE IT A LOOP") rendered into the dissolve
    zone. Clear the complete reserved title box, interpolating the surrounding
    overlay alpha across each row. Clearing the whole reservation removes the
    original glyphs and their dark anti-aliased shadows; glyph-only masking
    left a visible ghost behind newly rendered headlines. Arrows and the grid
    sit outside this reservation and remain untouched.
    """
    width, height = tpl.size
    x0 = int(width * _TEMPLATE_TEXT_BOX[0])
    y0 = int(height * _TEMPLATE_TEXT_BOX[1])
    x1 = int(width * _TEMPLATE_TEXT_BOX[2])
    y1 = int(height * _TEMPLATE_TEXT_BOX[3])
    px = tpl.load()
    left = max(x0 - 1, 0)
    right = min(x1, width - 1)
    span = max(x1 - x0, 1)
    for y in range(y0, y1):
        left_a = px[left, y][3]
        right_a = px[right, y][3]
        for offset, x in enumerate(range(x0, x1)):
            alpha = round(left_a + (right_a - left_a) * offset / span)
            px[x, y] = (0, 0, 0, alpha)
    return tpl


def _recolor_legacy_accents(tpl: Image.Image) -> Image.Image:
    """Convert the overlay's baked legacy-orange arrows to current lime."""
    px = tpl.load()
    for y in range(tpl.height):
        for x in range(tpl.width):
            r, g, b, a = px[x, y]
            if a and r > 120 and g > 55 and b < 120 and r > g * 1.15:
                px[x, y] = _ACCENT_GREEN[:3] + (a,)
    return tpl


def _load_scrubbed_template() -> Optional[Image.Image]:
    """Load the overlay template with its example text scrubbed (cached)."""
    path = settings.cover_overlay_template
    if not path.exists():
        return None
    key = (str(path), path.stat().st_mtime)
    cached = _SCRUBBED_TEMPLATE_CACHE.get(key)
    if cached is None:
        with Image.open(path) as tpl:
            cached = _recolor_legacy_accents(
                _scrub_template_text(tpl.convert("RGBA"))
            )
        _SCRUBBED_TEMPLATE_CACHE.clear()
        _SCRUBBED_TEMPLATE_CACHE[key] = cached
    return cached


def _build_overlay_png(title: str, highlight: str, wd: Path) -> Path:
    """Composite the overlay template + rendered title into one RGBA PNG.

    The template (2160x2700 = 2x output) is scaled to 1080x1350. If the
    template file is missing, a plain bottom-black gradient stands in so the
    pipeline still produces a reviewable cover.
    """
    width, height = settings.slide_width, settings.slide_height
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    template = _load_scrubbed_template()
    if template is not None:
        canvas.alpha_composite(
            template.resize((width, height), Image.Resampling.LANCZOS)
        )
    else:
        # Emergency stand-in: black rising from the bottom ~40%.
        ramp_top = int(height * 0.55)
        solid_top = int(height * 0.75)
        px = canvas.load()
        for y in range(ramp_top, height):
            if y >= solid_top:
                alpha = 255
            else:
                alpha = int(255 * (y - ramp_top) / max(solid_top - ramp_top, 1))
            for x in range(width):
                px[x, y] = (0, 0, 0, alpha)
    canvas.alpha_composite(_render_title_block(title, highlight))
    draw_slide_number(canvas, 1, fill=WARM_WHITE)
    out = wd / "overlay-composite.png"
    canvas.save(out)
    return out


def _center_crop_still(media_path: str, wd: Path) -> Path:
    """Scale/center-crop a still image to 2160x2700 (2x) for smooth zoompan."""
    target_w, target_h = settings.slide_width * 2, settings.slide_height * 2
    with Image.open(media_path) as img:
        img = img.convert("RGB")
        scale = max(target_w / img.width, target_h / img.height)
        new_size = (max(round(img.width * scale), target_w), max(round(img.height * scale), target_h))
        img = img.resize(new_size, Image.Resampling.LANCZOS)
        left = (img.width - target_w) // 2
        upper = (img.height - target_h) // 2
        img = img.crop((left, upper, left + target_w, upper + target_h))
        out = wd / "still-base.png"
        img.save(out)
    return out


def compose_cover(
    media_path: str, title: str, highlight: str, is_video: bool, workdir: str = ""
) -> dict:
    """Compose the final 1080x1350 cover video + poster from sourced media.

    The media is scaled/center-cropped to 1080x1350, the STRANGE-COVER overlay
    template plus the Pillow-rendered title block are composited on top, and
    ffmpeg renders a silent H.264 mp4 (``+faststart``) with a first-frame
    poster PNG. Still images become a 6 s very-slow-zoom video (static video
    fallback if the zoom filter fails).

    Args:
        media_path: Local path of the trimmed clip or downloaded image.
        title: Cover hook title (rendered uppercase in the shared inside-slide
            headline style, up to three balanced lines).
        highlight: Verbatim phrase inside ``title`` rendered in solid #B8EF43.
        is_video: True when ``media_path`` is a video clip.
        workdir: Run-specific working folder.

    Returns:
        Dict with ``video_path`` (str), ``poster_path`` (str) and
        ``duration_s`` (float).

    Raises:
        RuntimeError: When ffmpeg rendering fails.
        FileNotFoundError: When ``media_path`` does not exist.
    """
    require_no_em_dash([title, highlight], "cover copy")
    media = Path(media_path)
    if not media.exists():
        raise FileNotFoundError(f"media file not found: {media_path}")
    wd = _ensure_workdir(workdir, "cover")
    width, height = settings.slide_width, settings.slide_height

    overlay = _build_overlay_png(title, highlight, wd)
    out_video = wd / "cover.mp4"
    poster = wd / "cover-poster.png"

    encode_args = [
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "19",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
    ]

    if is_video:
        cap = float(settings.cover_clip_max_s)
        in_duration = _media_duration(media)
        clip_t = f"{min(in_duration, cap):.3f}" if in_duration > 0 else f"{cap:g}"
        filter_complex = (
            f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},setsar=1,fps={_COVER_FPS}[base];"
            "[base][1:v]overlay=0:0:format=auto[out]"
        )
        _run_ffmpeg(
            [
                settings.ffmpeg_bin,
                "-y",
                "-i",
                str(media),
                "-i",
                str(overlay),
                "-filter_complex",
                filter_complex,
                "-map",
                "[out]",
                "-t",
                clip_t,
                *encode_args,
                str(out_video),
            ]
        )
    else:
        base_png = _center_crop_still(str(media), wd)
        frames = int(_STILL_COVER_SECONDS * _COVER_FPS)
        zoom_filter = (
            "[0:v]zoompan=z='min(1+0.00045*on,1.08)'"
            ":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d={frames}:s={width}x{height}:fps={_COVER_FPS}[base];"
            "[base][1:v]overlay=0:0:format=auto[out]"
        )
        zoom_cmd = [
            settings.ffmpeg_bin,
            "-y",
            "-loop",
            "1",
            "-framerate",
            str(_COVER_FPS),
            "-i",
            str(base_png),
            "-i",
            str(overlay),
            "-filter_complex",
            zoom_filter,
            "-map",
            "[out]",
            "-frames:v",
            str(frames),
            *encode_args,
            str(out_video),
        ]
        try:
            _run_ffmpeg(zoom_cmd)
        except (RuntimeError, subprocess.TimeoutExpired):
            static_filter = (
                f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},setsar=1,fps={_COVER_FPS}[base];"
                "[base][1:v]overlay=0:0:format=auto[out]"
            )
            _run_ffmpeg(
                [
                    settings.ffmpeg_bin,
                    "-y",
                    "-loop",
                    "1",
                    "-framerate",
                    str(_COVER_FPS),
                    "-i",
                    str(base_png),
                    "-i",
                    str(overlay),
                    "-filter_complex",
                    static_filter,
                    "-map",
                    "[out]",
                    "-t",
                    f"{_STILL_COVER_SECONDS:.1f}",
                    *encode_args,
                    str(out_video),
                ]
            )

    _run_ffmpeg(
        [
            settings.ffmpeg_bin,
            "-y",
            "-i",
            str(out_video),
            "-frames:v",
            "1",
            str(poster),
        ],
        timeout_s=_FFPROBE_TIMEOUT_S,
    )
    duration = _media_duration(out_video)
    return {
        "video_path": str(out_video),
        "poster_path": str(poster),
        "duration_s": round(duration, 2),
    }
