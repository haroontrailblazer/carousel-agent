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
    "publisher_branding",
    "ai_illustration",
    "logo_only",
    "generic_stock",
    "unrelated",
    "low_quality",
)
# Problems that rule a candidate out whatever the model's verdict says. The
# cover must be real, sourced media; an AI picture or illustration never is.
_HARD_REJECT_PROBLEMS = ("ai_illustration",)
# A video only passes when this many sampled frames would each work as a
# cover. One good B-roll frame in a presenter-led news package is not a clip.
_MIN_USABLE_VIDEO_FRAMES = 3
# Wide subjects (group shots, side-by-side portraits) are laid across the top
# of the cover instead of being cropped to 4:5, which keeps one face at most.
_BAND_MIN_ASPECT = 1.2
_BAND_TARGET_ASPECT = 1080 / 756  # full width over the visible top ~56%
_BAND_FADE_FRACTION = 0.22
_FOCUS_MARGIN = 0.04
# A clean box smaller than this is a misread (the logo itself was boxed, say);
# cropping to it would blow a scrap of the frame up to cover size.
_CLEAN_MIN_AREA = 0.35
# Trimming a logo off an edge may cost a little of the subject. Losing more
# than this means the logo sits on the subject, and hiding it would cut the
# subject up or leave it out entirely.
_CLEAN_MIN_FOCUS_KEPT = 0.85
# The top of a subject box is where the faces are. A clean box may trim a
# loose top edge, but one that starts lower than this share of the subject's
# height cuts the heads to leave out a ticker or bug above them.
_CLEAN_MAX_TOP_CUT = 0.04


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
    # Only offer moments a cover clip can START on. A frame near the end of
    # the file cannot open a clip of the minimum length, and retrimming used
    # to slide the start earlier - so the approved frame never shipped.
    span = max(duration - float(settings.cover_clip_min_s), 0.0)
    times = [span * f for f in _SHEET_FRACTIONS] if span > 0 else [0.0]
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
- talking_head: a news anchor, host, presenter, YouTuber or interviewer who
  is not the story's subject
- publisher_branding: another news outlet's or channel's logo, watermark,
  channel bug, lower-third or burned-in caption that cannot be left outside
  the "clean" box below
- ai_illustration: an AI-generated picture, drawing, digital illustration
  or 3D render rather than a real photo or real footage
- logo_only, generic_stock, unrelated, low_quality

Several frames means a video. Judge the video, not just its luckiest frame:
a news channel's explainer or report (a presenter talking, their logo in a
corner, lower-thirds) is someone else's package and must be rejected even
if one frame shows useful B-roll. Official footage of the event, product,
demo or people themselves is what we want. Count how many frames would each
work as the cover on their own.

Answer with JSON only:
{"best_frame": <number on the frame's label>,
 "score": <0-10 for the best frame as a cover>,
 "verdict": "use" | "reject",
 "problems": [<zero or more of the problem names above, for the best frame>],
 "usable_frames": <how many of the frames would each work as the cover>,
 "focus": {"x": 0-1, "y": 0-1, "w": 0-1, "h": 0-1} or null,
 "clean": {"x": 0-1, "y": 0-1, "w": 0-1, "h": 0-1} or null,
 "reason": "<one short sentence>"}

All boxes are fractions of the best frame's own width and height (x, y is
the top-left corner).
focus: the box around the subject. Include the whole subject - every person
in a group shot, every face in side-by-side portraits - and nothing else.
Always give a box for a group shot, several people or side-by-side
portraits, even when it covers nearly the whole frame: that box is how the
cover knows to keep every one of them. Use null only for a scene with no
single subject to keep, where any 4:5 part of the frame would work.
clean: the largest box with NO logo, watermark, channel bug, lower-third,
caption or burned-in text from any outlet. It should hold the whole focus
box. When a corner bug or caption touches the subject, the clean box may
leave out a thin strip (up to about a tenth) of the focus box's bottom or of
one side, but never its top, where the faces are. Use null when the whole
frame is clean, or when no such box exists (then list publisher_branding).
"""


def _model_name() -> str:
    from app.services import ai_config

    name = ai_config.current().utility_model
    name = name.split("/", 1)[1] if name.startswith("openai/") else name
    return name.split("/", 1)[1] if name.startswith("responses/") else name


def _client() -> Any:
    from app.services import ai_config

    return ai_config.current().client.with_options(timeout=90)


def _box(raw: Any) -> Optional[dict]:
    """A 0-1 box clipped to the frame, or ``None`` when absent or degenerate."""
    if not isinstance(raw, dict):
        return None
    try:
        x, y = (min(max(float(raw[k]), 0.0), 1.0) for k in ("x", "y"))
        w = min(max(float(raw["w"]), 0.0), 1.0 - x)
        h = min(max(float(raw["h"]), 0.0), 1.0 - y)
    except (KeyError, TypeError, ValueError):
        return None
    if w <= 0.02 or h <= 0.02:
        return None
    return {"x": x, "y": y, "w": w, "h": h}


def _parse_verdict(text: str, frame_count: int) -> dict:
    match = re.search(r"\{.*\}", text or "", re.S)
    data = json.loads(match.group(0) if match else text)
    best = int(data.get("best_frame") or 1)
    best = min(max(best, 1), frame_count)
    score = min(max(int(data.get("score") or 0), 0), 10)
    verdict = "use" if str(data.get("verdict", "")).lower() == "use" else "reject"
    raw_problems = data.get("problems") or []
    if isinstance(raw_problems, str):  # one name instead of a list
        raw_problems = [raw_problems]
    named = (str(p).strip().lower() for p in raw_problems)
    problems = [p for p in named if p in PROBLEMS]
    try:
        usable = int(data.get("usable_frames"))
    except (TypeError, ValueError):
        usable = frame_count if verdict == "use" else 0
    usable = min(max(usable, 0), frame_count)
    reason = str(data.get("reason") or "")[:300]
    # Enforced here, not left to the model: a video where only one or two
    # sampled moments work is a presenter-led package with a little B-roll.
    if frame_count >= 4 and verdict == "use" and usable < _MIN_USABLE_VIDEO_FRAMES:
        verdict = "reject"
        reason = f"only {usable} of {frame_count} frames work as a cover. {reason}".strip()
    hard = [p for p in problems if p in _HARD_REJECT_PROBLEMS]
    if hard and verdict == "use":
        verdict = "reject"
        reason = f"not a real photo or footage ({', '.join(hard)}). {reason}".strip()
    focus = _box(data.get("focus"))
    clean = _box(data.get("clean"))
    if clean is not None and clean["w"] * clean["h"] > 0.97:
        clean = None
    return {
        "best_frame": best,
        "score": score,
        "verdict": verdict,
        "problems": problems,
        "usable_frames": usable,
        "focus": focus,
        "clean": clean,
        "reason": reason,
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
    ``_VISIBLE_TOP_FRACTION`` of the crop, above the title shadow. A subject
    taller than that area keeps its top (the faces) and loses its foot.

    Returns:
        ``(left, top, width, height)`` in source pixels, or ``None`` when the
        crop would be close to the whole source on both axes, or the focus is
        the whole frame (the renderer's own subject-aware crop already covers
        those cases).
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

    # A no-op only when the crop is (nearly) the whole source on BOTH axes, or
    # the focus is the whole frame and so carries no position to honour. A
    # full-height crop of a 16:9 frame (or a full-width one of a 9:16 frame)
    # still has to be PLACED, and the judge's box is what places it; the
    # renderer's saliency crop happily picks a busy slide over the person.
    whole_source = (
        crop_w >= source_w * _FOCUS_NOOP_FRACTION
        and crop_h >= source_h * _FOCUS_NOOP_FRACTION
    )
    whole_focus = focus["w"] >= _FOCUS_NOOP_FRACTION and focus["h"] >= _FOCUS_NOOP_FRACTION
    if whole_source or whole_focus:
        return None

    left = fx + fw / 2 - crop_w / 2
    top = min(fy + fh / 2 - crop_h * _VISIBLE_TOP_FRACTION / 2, fy)
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


@dataclass(frozen=True)
class Layout:
    """How the cover uses its source media, in source pixels.

    ``mode`` is ``"crop"`` (cut ``box`` out; the renderer fills the 4:5 cover
    with it) or ``"band"`` (scale ``box`` to the full cover width, lay it
    across the top at ``band_h`` pixels tall and fade it into the black title
    area). ``box`` is ``(left, top, width, height)``.
    """

    mode: str
    box: tuple[int, int, int, int]
    band_h: int = 0


def _even(value: float) -> int:
    return max(2, int(value) // 2 * 2)


def _trusted_clean(focus: Optional[dict], clean: Optional[dict]) -> Optional[dict]:
    """The clean box, or ``None`` when using it would cost the subject.

    The judge is asked for a clean box that holds the whole subject, or all
    but a thin strip of its bottom or one side, but that is impossible when a
    logo sits on the subject, and a verdict rejected for
    branding keeps its clean box too. The subject wins: the cover keeps the
    whole subject (and the logo) rather than a crop without the subject.
    A small trim is allowed off the bottom or a side (a lower-third, a corner
    bug beside the subject), but not off the top, where the heads are.
    """
    if clean is None:
        return None
    if clean["w"] * clean["h"] < _CLEAN_MIN_AREA:
        logger.warning("ignoring clean box %s: too small to be the logo-free area", clean)
        return None
    if focus is not None:
        ix = min(focus["x"] + focus["w"], clean["x"] + clean["w"]) - max(focus["x"], clean["x"])
        iy = min(focus["y"] + focus["h"], clean["y"] + clean["h"]) - max(focus["y"], clean["y"])
        if max(ix, 0.0) * max(iy, 0.0) < _CLEAN_MIN_FOCUS_KEPT * focus["w"] * focus["h"]:
            logger.warning("ignoring clean box %s: it would cut the subject %s", clean, focus)
            return None
        if clean["y"] - focus["y"] > _CLEAN_MAX_TOP_CUT * focus["h"]:
            logger.warning("ignoring clean box %s: it would cut the top of the subject %s", clean, focus)
            return None
    return clean


def plan_layout(
    source_w: int, source_h: int, focus: Optional[dict], clean: Optional[dict] = None
) -> Optional[Layout]:
    """Decide how to show the subject without burying it or cutting it up.

    Everything happens inside the clean area (the part of the frame with no
    other outlet's logo or captions). Then:

    - a subject that fits a 4:5 crop with room above the title shadow gets
      that crop (:func:`focus_crop_box`);
    - a wide subject that cannot - side-by-side portraits, a group on stage,
      or any subject wider than the widest 4:5 crop of the clean area -
      becomes a full-width band across the top. Cropping it to 4:5 kept one
      face, huge and blurry, with its mouth under the title, or cut the
      outer people through the shoulders;
    - with no subject box, the clean area is cropped out and the renderer's
      own subject-aware crop does the rest.

    A clean box that is tiny or would cut the subject is ignored
    (:func:`_trusted_clean`): the subject matters more than a logo.

    Returns:
        The layout, or ``None`` when the renderer's default crop is already
        right (no subject box, whole frame clean).
    """
    clean = _trusted_clean(focus, clean)
    cx, cy, cw, ch = 0.0, 0.0, float(source_w), float(source_h)
    if clean is not None:
        cx, cy = clean["x"] * source_w, clean["y"] * source_h
        cw, ch = clean["w"] * source_w, clean["h"] * source_h
    if focus is None:
        if clean is None:
            return None
        return Layout("crop", (int(cx), int(cy), _even(cw), _even(ch)))

    # Subject box in source pixels, clipped to the clean area, with a margin.
    fx0 = max(focus["x"] * source_w, cx)
    fy0 = max(focus["y"] * source_h, cy)
    fx1 = min((focus["x"] + focus["w"]) * source_w, cx + cw)
    fy1 = min((focus["y"] + focus["h"]) * source_h, cy + ch)
    if fx1 - fx0 < 8 or fy1 - fy0 < 8:  # a few pixels: nothing to place
        return Layout("crop", (int(cx), int(cy), _even(cw), _even(ch))) if clean else None
    subject_w = fx1 - fx0
    mx, my = (fx1 - fx0) * _FOCUS_MARGIN, (fy1 - fy0) * _FOCUS_MARGIN
    fx0, fy0 = max(fx0 - mx, cx), max(fy0 - my, cy)
    fx1, fy1 = min(fx1 + mx, cx + cw), min(fy1 + my, cy + ch)
    fw, fh = fx1 - fx0, fy1 - fy0

    aspect = settings.slide_width / settings.slide_height
    needed_h = max(fh / _VISIBLE_TOP_FRACTION, fw / aspect)
    # A group of two or three standing people is not very wide for its
    # height, but it can still be wider than any 4:5 crop of the frame, and
    # squeezing it into one cuts the outer people in half.
    too_wide = subject_w > ch * aspect
    if needed_h > ch and (fw / fh >= _BAND_MIN_ASPECT or too_wide):
        # Grow the band downward/upward toward the visible top area's shape,
        # never past the clean area, so the subject gets as much height as
        # the frame can give it.
        band_h = min(max(fh, fw / _BAND_TARGET_ASPECT), ch)
        top = min(max(fy0 + fh / 2 - band_h / 2, cy), cy + ch - band_h)
        width = max(fw, min(_MIN_FOCUS_CROP_W, cw))
        left = min(max(fx0 + fw / 2 - width / 2, cx), cx + cw - width)
        box = (int(left), int(top), _even(width), _even(band_h))
        out_h = _even(settings.slide_width * box[3] / box[2])
        return Layout("band", box, min(out_h, settings.slide_height))

    local = {
        "x": (fx0 - cx) / cw, "y": (fy0 - cy) / ch,
        "w": fw / cw, "h": fh / ch,
    }
    inner = focus_crop_box(int(cw), int(ch), local)
    if inner is None:
        if clean is None:
            return None
        return Layout("crop", (int(cx), int(cy), _even(cw), _even(ch)))
    left, top, width, height = inner
    return Layout("crop", (int(cx) + left, int(cy) + top, width, height))


def _band_fade(band_h: int, out: Path) -> Path:
    """A 1080x1350 overlay: clear over the band, fading to black at its foot."""
    width, height = settings.slide_width, settings.slide_height
    fade = max(90, round(band_h * _BAND_FADE_FRACTION))
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for row in range(max(band_h - fade, 0), height):
        progress = min(1.0, (row - (band_h - fade) + 1) / fade)
        draw.line([(0, row), (width, row)], fill=(0, 0, 0, round(255 * progress ** 1.3)))
    overlay.save(out)
    return out


def apply_focus(
    media_path: str,
    is_video: bool,
    focus: Optional[dict],
    workdir: str = "",
    clean: Optional[dict] = None,
) -> str:
    """Prepare the media for the cover according to :func:`plan_layout`.

    A crop layout returns the cropped media. A band layout returns media that
    is already 1080x1350 (the band on top, black below), which the renderer's
    4:5 fill then leaves untouched. Returns the input path when the default
    crop is already right.

    Raises:
        RuntimeError: When the media cannot be read or rendered.
    """
    media = Path(media_path)
    wd = media_tools._ensure_workdir(workdir, "inspect")
    stem = uuid.uuid4().hex[:10]
    width, height = settings.slide_width, settings.slide_height

    if not is_video:
        with Image.open(media) as image:
            source = image.convert("RGB")
        layout = plan_layout(source.width, source.height, focus, clean)
        if layout is None:
            return str(media)
        left, top, box_w, box_h = layout.box
        cropped = source.crop((left, top, left + box_w, top + box_h))
        out = wd / f"focus-{stem}.png"
        if layout.mode == "crop":
            cropped.save(out)
            return str(out)
        canvas = Image.new("RGB", (width, height), (0, 0, 0))
        canvas.paste(cropped.resize((width, layout.band_h), Image.Resampling.LANCZOS), (0, 0))
        fade = Image.open(_band_fade(layout.band_h, wd / f"fade-{stem}.png"))
        canvas.paste(fade, (0, 0), fade)
        canvas.save(out)
        return str(out)

    try:
        source_w, source_h = _video_size(media)
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"could not read video size: {exc}") from exc
    layout = plan_layout(source_w, source_h, focus, clean)
    if layout is None:
        return str(media)
    left, top, box_w, box_h = layout.box
    out = wd / f"focus-{stem}.mp4"
    encode = [
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out),
    ]
    if layout.mode == "crop":
        media_tools._run_ffmpeg(
            [settings.ffmpeg_bin, "-y", "-i", str(media),
             "-vf", f"crop={box_w}:{box_h}:{left}:{top}", *encode]
        )
        return str(out)
    fade = _band_fade(layout.band_h, wd / f"fade-{stem}.png")
    media_tools._run_ffmpeg(
        [
            settings.ffmpeg_bin, "-y", "-i", str(media), "-loop", "1", "-i", str(fade),
            "-filter_complex",
            f"[0:v]crop={box_w}:{box_h}:{left}:{top},scale={width}:{layout.band_h},"
            f"pad={width}:{height}:0:0:black,setsar=1[band];"
            "[band][1:v]overlay=0:0:format=auto:shortest=1[out]",
            "-map", "[out]", *encode,
        ]
    )
    return str(out)

