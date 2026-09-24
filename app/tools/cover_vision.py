"""Look at candidate cover media before it becomes the cover.

Every other step of cover sourcing ranks media by URL and page text, so
nothing ever checked what a candidate actually shows. That let a screen
recording of a PDF, a web page screenshot and a TV news anchor all pass as
"sourced video". This module closes that gap in three steps:

1. :func:`contact_sheet` lays out numbered frames of a video (or the still
   itself) on one image, so a single vision call can compare moments.
2. :func:`judge_cover_media` asks the workspace's utility model which frame
   makes the strongest cover, what is wrong with it, and where the subject is.
3. :func:`apply_focus` crops the media to that subject, placed in the upper
   part of the 4:5 cover so the title shadow does not bury it.

The vision call is the only billed step. It is cheap (one small image per
candidate) and optional: when it fails, the caller keeps the old behaviour.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

from PIL import Image, ImageDraw, ImageFont

from app.config import settings
from app.tools import media_tools

logger = logging.getLogger(__name__)

# Frames sampled from a video, spread across its length.
_SHEET_FRACTIONS = (0.05, 0.22, 0.39, 0.56, 0.73, 0.9)
_SHEET_COLUMNS = 3
_SHEET_CELL_W = 512
_STILL_MAX_SIDE = 1024
_SHEET_JPEG_QUALITY = 82

# The cover's black shadow starts at 48% and is solid from 66%, and the title
# sits below that. A subject has to land in the top part of the crop to be seen.
_VISIBLE_TOP_FRACTION = 0.56
# Never zoom into less than this many source pixels across; below it the
# cover turns to mush when scaled up to 1080 px.
_MIN_FOCUS_CROP_W = 480
# A focus crop that is nearly the whole usable frame adds nothing over the
# renderer's own subject-aware crop, so it is skipped.
_FOCUS_NOOP_FRACTION = 0.92

PROBLEMS = (
    "text_heavy",
    "document_or_slide",
    "screen_recording_or_webpage",
    "talking_head",
    "logo_only",
    "generic_stock",
    "unrelated",
    "low_quality",
)


@dataclass(frozen=True)
class Sheet:
    """A contact sheet ready to send, plus how to map its frames back."""

    path: Path
    timestamps: list[float]  # one per numbered frame; [0.0] for a still


def _extract_frame(media: Path, timestamp: float, out: Path) -> Optional[Image.Image]:
    try:
        media_tools._run_ffmpeg(
            [
                settings.ffmpeg_bin, "-y", "-ss", f"{max(timestamp, 0):.3f}",
                "-i", str(media), "-frames:v", "1", "-update", "1", str(out),
            ],
            timeout_s=60,
        )
        with Image.open(out) as image:
            return image.convert("RGB")
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        return None
    finally:
        out.unlink(missing_ok=True)


def _label(image: Image.Image, text: str) -> None:
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("arial.ttf", 30)
    except OSError:
        font = ImageFont.load_default()
    box = draw.textbbox((0, 0), text, font=font)
    draw.rectangle((0, 0, box[2] + 20, box[3] + 14), fill=(255, 214, 0))
    draw.text((10, 5), text, fill=(0, 0, 0), font=font)


def contact_sheet(media_path: str, is_video: bool, workdir: str = "") -> Sheet:
    """Lay the candidate out as numbered frames on one JPEG.

    A video contributes six frames spread across its length, so the judge can
    pick the moment and not just accept or reject the clip. A still is sent
    alone as frame 1.

    Raises:
        RuntimeError: When no frame could be read from the media.
    """
    media = Path(media_path)
    if not media.exists():
        raise RuntimeError(f"media file not found: {media_path}")
    wd = media_tools._ensure_workdir(workdir, "inspect")
    stem = uuid.uuid4().hex[:10]

    if not is_video:
        with Image.open(media) as image:
            still = image.convert("RGB")
        still.thumbnail((_STILL_MAX_SIDE, _STILL_MAX_SIDE))
        _label(still, "1")
        out = wd / f"sheet-{stem}.jpg"
        still.save(out, "JPEG", quality=_SHEET_JPEG_QUALITY)
        return Sheet(out, [0.0])

    duration = media_tools._media_duration(media)
    times = [duration * f for f in _SHEET_FRACTIONS] if duration > 0 else [0.0]
    frames: list[tuple[float, Image.Image]] = []
    for index, timestamp in enumerate(times):
        frame = _extract_frame(media, timestamp, wd / f"frame-{stem}-{index}.png")
        if frame is not None:
            frames.append((timestamp, frame))
    if not frames:
        raise RuntimeError(f"could not read any frame from {media_path}")

    first = frames[0][1]
    cell_h = max(1, round(_SHEET_CELL_W * first.height / first.width))
    columns = min(_SHEET_COLUMNS, len(frames))
    rows = -(-len(frames) // columns)
    gap = 8
    sheet = Image.new(
        "RGB",
        (columns * _SHEET_CELL_W + (columns - 1) * gap, rows * cell_h + (rows - 1) * gap),
        (40, 40, 40),
    )
    for index, (timestamp, frame) in enumerate(frames):
        cell = frame.resize((_SHEET_CELL_W, cell_h), Image.Resampling.LANCZOS)
        _label(cell, f"{index + 1}")
        sheet.paste(cell, ((index % columns) * (_SHEET_CELL_W + gap), (index // columns) * (cell_h + gap)))
    out = wd / f"sheet-{stem}.jpg"
    sheet.save(out, "JPEG", quality=_SHEET_JPEG_QUALITY)
    return Sheet(out, [timestamp for timestamp, _ in frames])


_JUDGE_SYSTEM = """\
You pick the image for the cover of an Instagram news carousel. The cover is
cropped to a 4:5 portrait. Its lower 40% is covered by a black shadow and a
big headline, so the subject must read in the upper part of the crop, at
phone size, in under a second.

A strong cover shows the real subject of the story: the person, product,
robot, device, place, object or scene the story is about, ideally doing
something. One clear focal point. Emotion or action beats a static shot.

Reject (verdict "reject") when the best frame is mainly:
- text_heavy: paragraphs, captions or UI text are the main content
- document_or_slide: a PDF, slide, paper page or chart
- screen_recording_or_webpage: a browser, app window or site screenshot,
  unless a clear photo or video of the subject inside it can be cropped out
  (then set focus on that photo and keep verdict "use")
- talking_head: a news anchor, host or presenter who is not the story's subject
- logo_only, generic_stock, unrelated, low_quality

Answer with JSON only:
{"best_frame": <number on the frame's label>,
 "score": <0-10 for the best frame as a cover>,
 "verdict": "use" | "reject",
 "problems": [<zero or more of the problem names above, for the best frame>],
 "focus": {"x": 0-1, "y": 0-1, "w": 0-1, "h": 0-1} or null,
 "reason": "<one short sentence>"}

focus is the box around the subject inside the best frame, as fractions of
that frame's own width and height (x, y is the top-left corner). Keep it
tight around what the viewer should see, but include the whole subject. Use
null when the whole frame is the subject or no crop would help.
"""


def _model_name() -> str:
    from app.services import ai_config

    name = ai_config.current().utility_model
    name = name.split("/", 1)[1] if name.startswith("openai/") else name
    return name.split("/", 1)[1] if name.startswith("responses/") else name


def _client() -> Any:
    from app.services import ai_config

    return ai_config.current().client.with_options(timeout=90)


def _parse_verdict(text: str, frame_count: int) -> dict:
    match = re.search(r"\{.*\}", text or "", re.S)
    data = json.loads(match.group(0) if match else text)
    best = int(data.get("best_frame") or 1)
    best = min(max(best, 1), frame_count)
    score = min(max(int(data.get("score") or 0), 0), 10)
    verdict = "use" if str(data.get("verdict", "")).lower() == "use" else "reject"
    problems = [p for p in (data.get("problems") or []) if p in PROBLEMS]
    focus = data.get("focus") or None
    if isinstance(focus, dict):
        try:
            focus = {k: min(max(float(focus[k]), 0.0), 1.0) for k in ("x", "y", "w", "h")}
            if focus["w"] <= 0.02 or focus["h"] <= 0.02:
                focus = None
        except (KeyError, TypeError, ValueError):
            focus = None
    else:
        focus = None
    return {
        "best_frame": best,
        "score": score,
        "verdict": verdict,
        "problems": problems,
        "focus": focus,
        "reason": str(data.get("reason") or "")[:300],
    }


def judge_cover_media(sheet: Sheet, story: str, hook: str, client: Any = None) -> dict:
    """Ask the utility model which frame makes the best cover, and why.

    Args:
        sheet: The contact sheet from :func:`contact_sheet`.
        story: The news title (and short summary) the cover is for.
        hook: The cover headline that will sit under the picture.
        client: An OpenAI client; the workspace client when omitted (tests
            inject a fake here).

    Returns:
        The parsed verdict (see :func:`_parse_verdict`).

    Raises:
        RuntimeError: When the call or its answer fails.
    """
    from app.observability import record_image_usage

    model = _model_name() if client is None else "test"
    client = client or _client()
    image_b64 = base64.b64encode(sheet.path.read_bytes()).decode("ascii")
    frames = len(sheet.timestamps)
    prompt = (
        f"Story: {story.strip()[:600]}\n"
        f"Cover headline: {hook.strip()}\n"
        f"The image holds {frames} numbered frame(s). Pick the best one for the cover."
    )
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": _JUDGE_SYSTEM},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_b64}", "detail": "high"},
                    },
                ],
            },
        ],
        "response_format": {"type": "json_object"},
    }
    if model.startswith("gpt-5"):
        kwargs["reasoning_effort"] = "low"
    try:
        response = client.chat.completions.create(**kwargs)
    except Exception as exc:  # the vision check is optional; never crash the cover
        raise RuntimeError(f"vision check failed: {exc}") from exc

    usage = getattr(response, "usage", None)
    if usage is not None:
        record_image_usage(
            model,
            "chat.completions.cover_vision",
            SimpleNamespace(
                input_tokens=getattr(usage, "prompt_tokens", 0),
                output_tokens=getattr(usage, "completion_tokens", 0),
                total_tokens=getattr(usage, "total_tokens", 0),
            ),
        )
    try:
        text = response.choices[0].message.content or ""
        return _parse_verdict(text, frames)
    except (IndexError, AttributeError, ValueError, TypeError) as exc:
        raise RuntimeError(f"vision check returned an unreadable answer: {exc}") from exc


def focus_crop_box(
    source_w: int, source_h: int, focus: dict
) -> Optional[tuple[int, int, int, int]]:
    """Turn a subject box into a 4:5 crop that keeps the subject visible.

    The subject is centred horizontally and placed inside the top
    ``_VISIBLE_TOP_FRACTION`` of the crop, above the title shadow.

    Returns:
        ``(left, top, width, height)`` in source pixels, or ``None`` when the
        crop would be close to the whole usable frame (the renderer's own
        subject-aware crop already covers that case).
    """
    aspect = settings.slide_width / settings.slide_height
    fx, fy = focus["x"] * source_w, focus["y"] * source_h
    fw, fh = focus["w"] * source_w, focus["h"] * source_h
    crop_h = max(fh / _VISIBLE_TOP_FRACTION, fw / aspect)
    crop_w = crop_h * aspect
    min_w = min(_MIN_FOCUS_CROP_W, source_w, source_h * aspect)
    if crop_w < min_w:
        crop_w, crop_h = min_w, min_w / aspect
    shrink = min(1.0, source_w / crop_w, source_h / crop_h)
    crop_w, crop_h = crop_w * shrink, crop_h * shrink

    full_w = min(source_w, source_h * aspect)
    if crop_w >= full_w * _FOCUS_NOOP_FRACTION:
        return None

    left = fx + fw / 2 - crop_w / 2
    top = fy + fh / 2 - crop_h * _VISIBLE_TOP_FRACTION / 2
    left = min(max(left, 0.0), source_w - crop_w)
    top = min(max(top, 0.0), source_h - crop_h)
    width, height = int(crop_w) // 2 * 2, int(crop_h) // 2 * 2  # even, for H.264
    return int(left), int(top), max(width, 2), max(height, 2)


def _video_size(media: Path) -> tuple[int, int]:
    proc = subprocess.run(
        [
            media_tools._ffprobe_bin(), "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(media),
        ],
        capture_output=True, text=True, timeout=60,
    )
    width, height = (proc.stdout or "").strip().split("x")[:2]
    return int(width), int(height)


def apply_focus(media_path: str, is_video: bool, focus: dict, workdir: str = "") -> str:
    """Crop the media to its subject; returns the input path when no crop helps.

    Raises:
        RuntimeError: When the crop cannot be rendered.
    """
    media = Path(media_path)
    wd = media_tools._ensure_workdir(workdir, "inspect")
    stem = uuid.uuid4().hex[:10]
    if not is_video:
        with Image.open(media) as image:
            source = image.convert("RGB")
        box = focus_crop_box(source.width, source.height, focus)
        if box is None:
            return str(media)
        left, top, width, height = box
        out = wd / f"focus-{stem}.png"
        source.crop((left, top, left + width, top + height)).save(out)
        return str(out)

    try:
        source_w, source_h = _video_size(media)
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"could not read video size: {exc}") from exc
    box = focus_crop_box(source_w, source_h, focus)
    if box is None:
        return str(media)
    left, top, width, height = box
    out = wd / f"focus-{stem}.mp4"
    media_tools._run_ffmpeg(
        [
            settings.ffmpeg_bin, "-y", "-i", str(media),
            "-vf", f"crop={width}:{height}:{left}:{top}",
            "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out),
        ]
    )
    return str(out)

