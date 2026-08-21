"""Media tools for the First-Page Visual agent.

Three jobs (see docs/CONTRACTS.md file map):

1. ``find_source_clip(news)``   - pick the best sourced video (preferred) or
   image URL from a news item's ``media_urls``, its source page, every page
   linked in its body text, and - when nothing sourced plays - a bounded web
   search for an event clip. ``placeholder_background`` guarantees a cover can
   ALWAYS be built even with zero media found.
2. ``download_and_trim(url)``   - fetch the clip via the yt-dlp Python API and
   trim it into the configured cover window (settings.cover_clip_min_s..max_s,
   default 4-15 s), silent H.264 mp4.
3. ``compose_cover(...)``       - scale/center-crop the media to 1080x1350,
   composite the STRANGE-COVER overlay template plus a Pillow-rendered title
   block (warm-white, condensed extra-bold uppercase, lime-gradient highlight
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
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

import requests
from PIL import Image, ImageDraw, ImageFont
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError, download_range_func

from app.config import settings
from app.text_rules import require_no_em_dash

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

# Title styling (current baskaranbuilds.com tokens; skills/cover-style.md).
_TEXT_PRIMARY = (232, 228, 214, 255)  # #E8E4D6
_PRIMARY_START = (200, 237, 121)  # #C8ED79
_PRIMARY_END = (184, 239, 67)  # #B8EF43
_TITLE_MAX_LINES = 2
_TITLE_MAX_WIDTH_FRAC = 0.63  # inner span between the template's arrow glyphs
_TITLE_CENTER_Y_FRAC = 0.79  # matches the template's own title-block center

# Region of the template occupied by its baked-in EXAMPLE title text
# ("STOP PROMPTING YOUR AI, GIVE IT A LOOP") - measured on the shipped
# STRANGE-COVER (1).png. It is scrubbed before compositing the real title.
# Fractions of width/height; excludes the side arrow glyphs (0.093-0.167 and
# 0.839-0.907) and the grid floor.
_TEMPLATE_TEXT_BOX = (0.175, 0.705, 0.83, 0.872)  # (x0, y0, x1, y1)
_TEXT_LUMA_THRESHOLD = 6  # max(R,G,B) above this inside the box = text pixel
_TEXT_SCRUB_DILATION_X = 3  # px - also scrub the anti-aliased glyph edge ring
_TEXT_SCRUB_DILATION_Y = 2
_FONT_CANDIDATES = (
    Path("C:/Windows/Fonts/bahnschrift.ttf"),
    Path("C:/Windows/Fonts/impact.ttf"),
    Path("C:/Windows/Fonts/arialbd.ttf"),
)
_FALLBACK_FONT_NAME = "DejaVuSans-Bold.ttf"

_STILL_COVER_SECONDS = 6.0
_COVER_FPS = 30


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
        'body_page' | 'web_search' | ''), and ``note`` (str).
    """
    candidates: list[tuple[str, str, int, str]] = []  # (url, kind, score, origin)
    seen: set[str] = set()

    def add(url: str, kind: str, score: int, origin: str) -> None:
        key = url.split("#", 1)[0]
        if key and key not in seen:
            seen.add(key)
            candidates.append((url, kind, score, origin))

    for url in news.get("media_urls") or []:
        if not isinstance(url, str) or not url.strip():
            continue
        url = url.strip()
        kind = _classify_url(url)
        score = {"video": 100, "image": 50}.get(kind, 25)
        add(url, kind, score, "media_urls")

    source_url = str(news.get("source_url") or "").strip()
    if source_url and _classify_url(source_url) == "video":
        add(source_url, "video", 95, "source_url")
    if source_url:
        for url, kind, score in _scrape_page_media(source_url):
            add(url, kind, score, "source_page")

    # Pages linked inside the body/summary text (ad-hoc runs put the links
    # there): direct media URLs become candidates, other pages are scraped
    # for og:image / og:video just like the source page.
    body_text = " ".join(
        str(news.get(k) or "") for k in ("title", "summary", "body")
    )
    scraped_pages = 0
    for raw in _URL_IN_TEXT_RE.findall(body_text):
        url = raw.rstrip(".,;:!?")
        if url.split("#", 1)[0] in seen or url == source_url:
            continue
        kind = _classify_url(url)
        if kind == "video":
            add(url, "video", 92, "body_url")
        elif kind == "image":
            add(url, "image", 48, "body_url")
        elif scraped_pages < _BODY_URL_SCRAPE_LIMIT:
            scraped_pages += 1
            for media_url, media_kind, score in _scrape_page_media(url):
                add(media_url, media_kind, max(score - 2, 1), "body_page")

    candidates.sort(key=lambda c: c[2], reverse=True)

    # Best image candidate - reported alongside every result so the caller
    # can drop to the image path the moment video downloads fail, without
    # re-searching.
    image_url, image_origin = "", ""
    for url, kind, _score, origin in candidates:
        if kind == "image":
            image_url, image_origin = url, origin
            break

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
            "note": note,
        }

    # Confirm video candidates (bounded number of network probes).
    probes = 0
    for url, kind, _score, origin in candidates:
        if kind not in ("video", "unknown"):
            continue
        if probes >= _MAX_PROBES:
            break
        probes += 1
        info = _probe_with_ytdlp(url)
        if info is not None:
            return result(
                True, url, True, round(info["duration"], 2), origin,
                info["title"] or "probed with yt-dlp",
            )
        if _url_ext(url) in VIDEO_EXTS:
            # Direct video file that yt-dlp could not probe (e.g. signed CDN
            # URL) - trust the extension and let download_and_trim try.
            return result(
                True, url, True, 0.0, origin,
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
    except OSError as exc:
        raise RuntimeError(f"downloaded file is not a readable image: {url}") from exc
    return str(path)


# ---------------------------------------------------------------------------
# 3) compose_cover - title rendering + overlay + ffmpeg render
# ---------------------------------------------------------------------------


def _load_title_font(size: int) -> ImageFont.FreeTypeFont:
    """Load the title font at ``size`` following the fallback chain.

    Bahnschrift is a variable font: when it loads, the weight axis is pushed
    to extra-bold and the width axis to condensed per skills/cover-style.md.
    """
    for path in _FONT_CANDIDATES:
        if not path.exists():
            continue
        try:
            font = ImageFont.truetype(str(path), size)
        except OSError:
            continue
        if "bahnschrift" in path.name.lower():
            try:
                axes = font.get_variation_axes()
                values: list[float] = []
                for axis in axes:
                    name = axis.get("name", b"")
                    if isinstance(name, bytes):
                        name = name.decode("ascii", errors="ignore")
                    lowered = name.lower()
                    if "weight" in lowered or lowered == "wght":
                        values.append(min(float(axis["maximum"]), 700.0))
                    elif "width" in lowered or lowered == "wdth":
                        values.append(max(float(axis["minimum"]), 75.0))
                    else:
                        values.append(float(axis["default"]))
                if values:
                    font.set_variation_by_axes(values)
            except OSError:
                pass
        return font
    try:
        return ImageFont.truetype(_FALLBACK_FONT_NAME, size)
    except OSError:
        return ImageFont.load_default(size=size)  # type: ignore[return-value]


def _line_width(font: ImageFont.FreeTypeFont, text: str) -> float:
    """Width of ``text`` measured char-by-char (matches per-char drawing)."""
    return sum(font.getlength(ch) for ch in text)


def _wrap_title(title: str, font: ImageFont.FreeTypeFont, max_w: float) -> list[str]:
    """Split the title into at most two visually balanced lines.

    Returns the best split (minimizing the wider line) when one line does not
    fit; single-word or already-fitting titles stay on one line.
    """
    if _line_width(font, title) <= max_w:
        return [title]
    words = title.split(" ")
    if len(words) < 2:
        return [title]
    best: tuple[float, list[str]] = (float("inf"), [title])
    for i in range(1, len(words)):
        l1 = " ".join(words[:i])
        l2 = " ".join(words[i:])
        widest = max(_line_width(font, l1), _line_width(font, l2))
        if widest < best[0]:
            best = (widest, [l1, l2])
    return best[1]


def _gradient_color(t: float) -> tuple[int, int, int, int]:
    """Interpolate the current lime highlight gradient at ``t`` in [0, 1]."""
    t = min(max(t, 0.0), 1.0)
    r = round(_PRIMARY_START[0] + (_PRIMARY_END[0] - _PRIMARY_START[0]) * t)
    g = round(_PRIMARY_START[1] + (_PRIMARY_END[1] - _PRIMARY_START[1]) * t)
    b = round(_PRIMARY_START[2] + (_PRIMARY_END[2] - _PRIMARY_START[2]) * t)
    return (r, g, b, 255)


def _render_title_block(title: str, highlight: str) -> Image.Image:
    """Render the cover title onto a transparent 1080x1350 RGBA image.

    Centered in the lower third, max two lines, condensed extra-bold
    uppercase, white, with the highlight phrase in a per-character horizontal
    lime gradient (#C8ED79 -> #B8EF43).
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
    font = _load_title_font(88)
    lines = [text]
    for size in range(88, 32, -4):
        font = _load_title_font(size)
        lines = _wrap_title(text, font, max_w)
        if len(lines) <= _TITLE_MAX_LINES and all(
            _line_width(font, ln) <= max_w for ln in lines
        ):
            break

    draw = ImageDraw.Draw(canvas)
    ascent, descent = font.getmetrics()
    line_h = ascent + descent
    gap = int(line_h * 0.12)
    total_h = len(lines) * line_h + (len(lines) - 1) * gap
    top = int(height * _TITLE_CENTER_Y_FRAC - total_h / 2)
    top = min(top, height - int(height * 0.06) - total_h)  # keep off the grid floor

    global_idx = 0  # char index into `text` (lines re-join with single spaces)
    span_len = max(hl_end - hl_start, 1)
    for line_no, line in enumerate(lines):
        y = top + line_no * (line_h + gap)
        x = (width - _line_width(font, line)) / 2
        for ch in line:
            if hl_start <= global_idx < hl_end:
                t = (global_idx - hl_start) / max(span_len - 1, 1)
                color = _gradient_color(t)
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
    zone. Every text-colored pixel inside ``_TEMPLATE_TEXT_BOX`` is replaced
    with pure black whose alpha is linearly interpolated from the nearest
    non-text background pixels on the same row - seamless even where the
    grain dissolve is only partially opaque. Arrows and grid are untouched.
    """
    width, height = tpl.size
    x0 = int(width * _TEMPLATE_TEXT_BOX[0])
    y0 = int(height * _TEMPLATE_TEXT_BOX[1])
    x1 = int(width * _TEMPLATE_TEXT_BOX[2])
    y1 = int(height * _TEMPLATE_TEXT_BOX[3])
    px = tpl.load()

    # Pass 1: per-row runs of text pixels, dilated horizontally so the
    # anti-aliased edge ring (near-black but higher-alpha) is caught too.
    row_runs: dict[int, list[tuple[int, int]]] = {}
    for y in range(y0, y1):
        runs: list[tuple[int, int]] = []
        start: Optional[int] = None
        for x in range(x0, x1):
            r, g, b, _a = px[x, y]
            if max(r, g, b) > _TEXT_LUMA_THRESHOLD:
                if start is None:
                    start = x
            elif start is not None:
                runs.append((start - _TEXT_SCRUB_DILATION_X, x + _TEXT_SCRUB_DILATION_X))
                start = None
        if start is not None:
            runs.append((start - _TEXT_SCRUB_DILATION_X, x1 + _TEXT_SCRUB_DILATION_X))
        row_runs[y] = runs

    # Pass 2: vertical dilation (merge neighbour rows' runs), then repaint each
    # run black with alpha interpolated between its just-outside anchors.
    for y in range(y0, y1):
        collected: list[tuple[int, int]] = []
        for yy in range(max(y0, y - _TEXT_SCRUB_DILATION_Y), min(y1, y + _TEXT_SCRUB_DILATION_Y + 1)):
            collected.extend(row_runs.get(yy, ()))
        merged: list[list[int]] = []
        for s, e in sorted(collected):
            if merged and s <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], e)
            else:
                merged.append([s, e])
        for s, e in merged:
            s = max(s, 1)
            e = min(e, width - 1)
            left_a = px[s - 1, y][3]
            right_a = px[e, y][3]
            span = e - s
            for i, rx in enumerate(range(s, e)):
                alpha = round(left_a + (right_a - left_a) * (i + 1) / (span + 1))
                px[rx, y] = (0, 0, 0, alpha)
    return tpl


def _recolor_legacy_accents(tpl: Image.Image) -> Image.Image:
    """Convert the overlay's baked legacy-orange arrows to current lime."""
    px = tpl.load()
    for y in range(tpl.height):
        for x in range(tpl.width):
            r, g, b, a = px[x, y]
            if a and r > 120 and g > 55 and b < 120 and r > g * 1.15:
                strength = max(r, g, b) / 255
                px[x, y] = (
                    round(_PRIMARY_END[0] * strength),
                    round(_PRIMARY_END[1] * strength),
                    round(_PRIMARY_END[2] * strength),
                    a,
                )
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
        title: Cover hook title (rendered uppercase, max two lines).
        highlight: Verbatim phrase inside ``title`` rendered in lime gradient.
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
