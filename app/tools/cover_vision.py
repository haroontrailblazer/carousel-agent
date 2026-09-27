"""Look at candidate cover media before it becomes the cover.

Every other step of cover sourcing ranks media by URL and page text, so
nothing ever checked what a candidate actually shows. That let a screen
recording of a PDF, a web page screenshot and a TV news anchor all pass as
"sourced video". This module closes that gap in three steps:

1. :func:`contact_sheet` lays out numbered frames of a video (or the still
   itself) on one image, so a single vision call can compare moments.
2. :func:`judge_cover_media` asks the workspace's utility model which frame
   makes the strongest cover, what is wrong with it, where the subject is,
   and what kind of picture it is (:data:`KINDS`): a real photo, the story
   subject's own official visual, a text graphic, or third-party AI art.
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
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional
from urllib.parse import urlparse

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
# What the picture is, which decides whether it may become the cover at all.
# A company's own key art or product render is sourced (the subject published
# it; we generated nothing), so only AI art made by a third party, such as a
# news site's generated header, is ruled out whatever the verdict says.
KINDS = ("real_photo", "official_visual", "text_graphic", "ai_art", "unusable")
_TEXT_PROBLEMS = frozenset({"document_or_slide", "text_heavy", "screen_recording_or_webpage"})
_UNUSABLE_PROBLEMS = frozenset({"logo_only", "unrelated", "talking_head", "low_quality"})
# How sure the code is that a picture's page belongs to the story's own
# organisation (see official_source). Only an exact match lets the company's
# own key art count as sourced when the judge calls it AI art.
OFFICIAL_EXACT = "exact"
OFFICIAL_FUZZY = "fuzzy"
# Parts of a URL host that never make an image the story subject's own: news
# outlets (fan sites named after a brand too: appleinsider starts with
# "apple"), platforms, shared hosting and Wikimedia (anyone uploads there).
_NEVER_OFFICIAL_LABELS = frozenset({
    "abc", "aljazeera", "amazonaws", "androidauthority", "androidcentral", "androidpolice",
    "apnews", "appleinsider", "arstechnica", "axios", "bbc", "bbci", "blogspot", "bloomberg",
    "businessinsider", "cbsnews", "cloudfront", "cnbc", "cnet", "cnn", "engadget", "facebook",
    "forbes", "foxnews", "gizmodo", "githubusercontent", "googleusercontent", "guardian",
    "imgur", "instagram", "linkedin", "mashable", "medium", "msn", "nbcnews", "nytimes",
    "pinimg", "reddit", "reuters", "scmp", "skynews", "staticflickr", "substack", "techcrunch",
    "techradar", "teslarati", "theguardian", "theregister", "theverge", "tiktok", "twimg",
    "twitter", "venturebeat", "washingtonpost", "wikimedia", "wikipedia", "windowscentral",
    "wired", "wordpress", "wp", "wsj", "yahoo", "youtube", "ytimg", "zdnet",
})
# Words that sit in fan and news site names next to the brand they cover
# (xiaomitoday.it, xiaomitime.com, openaimaster.com). A host that only
# resembles a subject's name is never its own when it holds one of them.
_FAN_SITE_WORDS = (
    "blog", "central", "daily", "fans", "forum", "geek", "guide", "hub", "insider", "leaks",
    "magazine", "master", "news", "report", "rumor", "rumour", "time", "today", "tips",
    "update", "week", "world", "zone",
)
# What a company's own asset hosts add to its name (mistralcdn, openaiassets).
_OFFICIAL_HOST_SUFFIXES = ("cdn", "static", "assets", "media", "ai", "labs", "hq")
# Words of a subject name that never identify it alone ("Mistral Small",
# "Services Australia"), and company-form words dropped from a full name.
_GENERIC_TOKENS = frozenset({
    "about", "after", "also", "and", "audio", "breaking", "business", "but", "culture",
    "earth", "flash", "for", "from", "health", "home", "how", "large", "live", "max",
    "medium", "mini", "model", "models", "new", "news", "official", "open", "portal", "pro",
    "said", "service", "services", "small", "sport", "system", "technology", "that", "the",
    "their", "these", "this", "today", "travel", "ultra", "video", "watch", "weather",
    "what", "when", "where", "which", "who", "why", "with", "world",
})
_COMPANY_FORM_WORDS = ("ai", "inc", "labs", "lab", "group", "corp", "corporation", "company",
                       "technologies", "technology", "ltd", "limited", "hq")
# Country suffixes with two parts: the name sits one label further left.
_TWO_PART_SUFFIXES = frozenset({
    "ac.uk", "co.in", "co.jp", "co.kr", "co.nz", "co.uk", "co.za", "com.au", "com.br",
    "com.cn", "com.hk", "com.sg", "com.tw", "edu.au", "gov.au", "gov.cn", "gov.in",
    "gov.sg", "gov.uk", "govt.nz", "net.au", "org.au", "org.uk",
})
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

The message says where the picture was found and whether that page belongs
to the story's own organisation. Say what kind of picture the best frame is:
- real_photo: a camera photo or real footage of the story's subject, of a
  person, place, building, product or event the story names (a Wikimedia
  Commons photo of one of them included), or the story article's own photo
- official_visual: key art, a product or device render, a UI render or
  keynote graphic with little text, or demo or launch footage published by
  the company, lab or government the story is about. It is sourced whatever
  tool made it: the subject published it, nobody generated it for this cover
- text_graphic: a chart, benchmark table, slide, document, text card, or a
  page or UI screenshot where the text is the content
- ai_art: AI-generated or illustrative art made by a third party, such as a
  news site's generated header picture
- unusable: unrelated, a logo alone, tiny or blurred, or another outlet's
  presenter

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
  the "clean" box below. The story subject's own logo or name (the company,
  product or team the story is about) is not publisher_branding
- ai_illustration: AI-generated art, a drawing or a digital illustration
  made by a third party, such as a news site's generated header. Key art,
  renders and demo footage published by the story's own organisation are
  official_visual, not ai_illustration
- logo_only, generic_stock, unrelated, low_quality

A chart, slide or page that holds a photo or render covering about a third
of the frame or more: box that photo or render as focus, judge that region
as the cover, and take the kind from the region.

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
 "kind": "real_photo" | "official_visual" | "text_graphic" | "ai_art" | "unusable",
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


def official_level(value: Any) -> str:
    """:data:`OFFICIAL_EXACT`, :data:`OFFICIAL_FUZZY` or '' for any stored form.

    Records from before the levels existed hold a bool; True meant the full
    override was applied, so it reads as exact.
    """
    if value is True:
        return OFFICIAL_EXACT
    text = str(value or "").strip().lower()
    return text if text in (OFFICIAL_EXACT, OFFICIAL_FUZZY) else ""


def kind_from_problems(problems: Sequence[str], verdict: str = "reject") -> str:
    """The kind a verdict implies when the model gave none (older verdicts).

    A text problem makes a text graphic whatever the verdict. An unusable
    problem (a presenter, a logo, something unrelated) only decides a
    rejection: an approved photo with a presenter in it is still a photo. A
    rejection that names no known problem says nothing about the picture, so
    it is not assumed to be a real photo (that would rank it first).
    """
    named = {str(problem).strip().lower() for problem in problems or ()}
    rejected = str(verdict or "").strip().lower() != "use"
    if "ai_illustration" in named:
        return "ai_art"
    if named & _TEXT_PROBLEMS:
        return "text_graphic"
    if rejected and named & _UNUSABLE_PROBLEMS:
        return "unusable"
    if rejected and not named & set(PROBLEMS):
        return "unusable"
    return "real_photo"


def _rejected_kind(kind: str, problems: Sequence[str]) -> str:
    """The kind of a REJECTED picture, from its problems, not the model's label.

    A "real photo" rejected as a presenter, a logo or unrelated is none of
    those things for a cover, and one rejected as a slide, a page or a text
    card is a text graphic (a vendor's share card must not pass as a photo or
    as official art).
    """
    named = set(problems)
    if kind == "ai_art":
        return kind
    if named & _UNUSABLE_PROBLEMS:
        return "unusable"
    if named & _TEXT_PROBLEMS:
        return "text_graphic"
    return kind


def tier_for(
    kind: str, origin: str = "", problems: Sequence[str] = (), official: Any = "",
) -> int:
    """How far down the sourcing ladder a picture sits; higher is better.

    3 is a real photo of the story, 2 an official visual from the subject's
    own site or a Wikimedia reference photo (real, but of the subject in
    general, not of this news), 1 a text graphic (a chart or card, weak as a
    cover), an official-looking visual whose page is not verifiably the
    subject's own, or anything flagged generic stock; 0 is never a cover.
    ``official`` is the page's :func:`official_source` level.
    """
    if kind == "real_photo":
        tier = 2 if origin == "wikimedia" else 3
    elif kind == "official_visual":
        # Fan sites carry AI art in the company's style; only the company's
        # own site vouches for it.
        tier = 2 if official_level(official) == OFFICIAL_EXACT else 1
    else:
        tier = 1 if kind == "text_graphic" else 0
    if "generic_stock" in {str(problem).strip().lower() for problem in problems or ()}:
        tier = min(tier, 1)
    return tier


def _host_label(url: str) -> str:
    """The name part of a URL's host: 'mistral' for cdn.mistral.ai."""
    text = str(url or "").strip().lower()
    host = urlparse(text if "//" in text else f"//{text}").hostname or ""
    parts = [part for part in host.split(".") if part]
    if len(parts) < 2:
        return re.sub(r"[^a-z0-9]", "", parts[0]) if parts else ""
    index = -3 if len(parts) >= 3 and ".".join(parts[-2:]) in _TWO_PART_SUFFIXES else -2
    return re.sub(r"[^a-z0-9]", "", parts[index])


def _host(url: str) -> str:
    text = str(url or "").strip().lower()
    host = urlparse(text if "//" in text else f"//{text}").hostname or ""
    return host[4:] if host.startswith("www.") else host


def _squashed(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def _subject_names(subjects: Sequence[str]) -> tuple[set[str], set[str]]:
    """Each subject's whole name run together, and its distinctive words.

    'Mistral AI' gives the names 'mistralai' and 'mistral' (a company-form
    word dropped), and 'Mistral Small 4' gives 'mistralsmall4',
    'mistralsmall' and 'mistral' (a version and a size word dropped).
    'Services Australia' gives 'servicesaustralia' and the word 'australia'
    ('services' names nothing); 'Anthony Albanese' never becomes 'anthony'.
    """
    names: set[str] = set()
    words: set[str] = set()
    for subject in subjects or ():
        parts = [part for part in re.findall(r"[a-z0-9]+", str(subject or "").lower()) if part]
        if not parts:
            continue
        names.add("".join(parts))
        while len(parts) > 1 and (
            parts[-1] in _COMPANY_FORM_WORDS
            or parts[-1] in _GENERIC_TOKENS
            or re.fullmatch(r"v?\d+[a-z]?", parts[-1])
        ):
            parts = parts[:-1]
            names.add("".join(parts))
        words.update(
            part for part in parts
            if len(part) >= 3 and any(ch.isalpha() for ch in part) and part not in _GENERIC_TOKENS
        )
    return {name for name in names if len(name) >= 3 and name not in _GENERIC_TOKENS}, words


def official_source(
    urls: Sequence[str],
    subjects: Sequence[str],
    publishers: Sequence[str] = (),
    sites: Sequence[str] = (),
) -> str:
    """How surely a picture comes from the story subject's own site.

    Matched on the subject NAMES (reference_photos.story_subjects), never on
    every capitalised word of the page: that made xiaomitoday.it,
    speedtest.net ("SPEED" in a shouted title) and standard.co.uk ("Standard"
    in a BBC menu) the subject's own.

    - :data:`OFFICIAL_EXACT`: the host is a subject's official website on
      Wikidata (P856, passed as ``sites``: mi.com for Xiaomi, openai.com for
      OpenAI), or its name part and domain together spell a subject's whole
      name (liquid.ai for "Liquid AI", cms.mistral.ai for "Mistral AI"; an
      .ai domain spells the company form, so mistral.ai for "Mistral" too)
      and no known official website of that name sits on another host.
    - :data:`OFFICIAL_FUZZY`: it only resembles one: the name part alone is
      a subject's name on some domain (xiaomi.eu is a community ROM site,
      liquid.com a crypto exchange, medicare.gov the US scheme in a story
      about Australia's; only P856 tells them from the real sites), two
      subject words run together (xiaomimimo.com for Xiaomi and MiMo), a
      subject plus a CDN word (openaicdn), or one word of a longer name. Fan
      sites look exactly like that, so a fuzzy match never turns "AI art"
      into official art.
    - '': anything else, any news outlet, platform or fan site, and a host
      whose name a known official website uses on another domain (liquid.com
      when Liquid AI's P856 is liquid.ai).

    A publisher that reported the story (its URL or name) is excluded unless
    it is itself a subject: a company's own press page is official.
    """
    names, words = _subject_names([subjects] if isinstance(subjects, str) else subjects)
    if not names and not words and not sites:
        return ""
    excluded: set[str] = set()
    for publisher in publishers or ():
        text = str(publisher or "").strip()
        if not text:
            continue
        label = _host_label(text) if ("/" in text or ("." in text and " " not in text)) else _squashed(text)
        if label and label not in names:
            excluded.add(label)
    official_hosts = {_host(site) for site in sites or () if _host(site)}
    official_labels = {_host_label(site) for site in official_hosts}
    best = ""
    for url in urls or ():
        host = _host(url)
        label = _host_label(url)
        if not label or label in _NEVER_OFFICIAL_LABELS:
            continue
        if any(host == site or host.endswith("." + site) for site in official_hosts):
            return OFFICIAL_EXACT
        if label in excluded or label in official_labels:
            # The subject's own site of this name is elsewhere (liquid.ai).
            continue
        tld = host.rsplit(".", 1)[-1] if "." in host else ""
        if f"{label}{tld}" in names or (tld == "ai" and label in names):
            # The domain spells the company's form: mistral.ai is "Mistral
            # AI" for a story that only says "Mistral" (Wikidata's "Mistral"
            # is a warship, so P856 cannot confirm it).
            return OFFICIAL_EXACT
        if label in names:
            best = OFFICIAL_FUZZY
            continue
        if any(word in label for word in _FAN_SITE_WORDS):
            continue
        stems = names | words
        if (
            label in words
            or any(label == stem + suffix for stem in stems for suffix in _OFFICIAL_HOST_SUFFIXES)
            or any(label.startswith(first) and label[len(first):] in stems for first in stems)
        ):
            best = OFFICIAL_FUZZY
    return best


def _parse_verdict(text: str, frame_count: int, official: Any = "") -> dict:
    """The judge's answer as a verdict dict; ``official`` is an official_source level.

    The code, not the model, has the last word on the kind: the official
    override comes first (only for an exact match), then a rejected picture
    takes its kind from its problems (:func:`_rejected_kind`), and last AI
    art made by a third party is rejected whatever the verdict said.
    """
    official = official_level(official)
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
    kind = str(data.get("kind") or "").strip().lower()
    if kind not in KINDS:
        kind = kind_from_problems(problems, verdict)
    exact = official == OFFICIAL_EXACT
    if "ai_illustration" in problems and not exact:
        kind = "ai_art"
    if exact:
        # The story's own company published it: its key art is sourced, and
        # its own logo on it is not another outlet's branding.
        overruled = kind == "ai_art" or "ai_illustration" in problems
        if kind == "ai_art":
            kind = "official_visual"
            problems = [p for p in problems if p != "publisher_branding"]
        problems = [p for p in problems if p != "ai_illustration"]
        if overruled and not problems:
            # Its only fault was looking AI-made: Mistral's key art on
            # mistral.ai stayed "rejected" and the QA gate re-ran the step.
            verdict = "use"
    elif kind == "ai_art" and "ai_illustration" not in problems:
        problems.append("ai_illustration")
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
    # Last, after the official override: a rejected "real photo" of a
    # presenter is unusable, and a vendor's text card or page screenshot is
    # a text graphic, whatever it was called (text_heavy included).
    if verdict == "reject":
        kind = _rejected_kind(kind, problems)
    # The cover must be real, sourced media: AI art by a third party never is.
    if kind == "ai_art" and verdict == "use":
        verdict = "reject"
        reason = f"not a real photo or footage (ai_illustration). {reason}".strip()
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
        "kind": kind,
    }


def judge_cover_media(
    sheet: Sheet,
    story: str,
    hook: str,
    client: Any = None,
    *,
    provenance: str = "",
    official: Any = "",
) -> dict:
    """Ask the utility model which frame makes the best cover, and why.

    Args:
        sheet: The contact sheet from :func:`contact_sheet`.
        story: The news title (and short summary) the cover is for.
        hook: The cover headline that will sit under the picture.
        client: An OpenAI client; the workspace client when omitted (tests
            inject a fake here).
        provenance: Where the media was found (page and origin), shown to the
            judge as "Found on".
        official: The :func:`official_source` level of the media's pages.
            Exact: its key art is an official visual, never third-party AI
            art. Fuzzy: the judge is told the site only resembles the
            subject's, and nothing is overridden.

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
    found_on = f"Found on: {provenance.strip()[:300]}\n" if provenance.strip() else ""
    level = official_level(official)
    own = {
        OFFICIAL_EXACT: "yes",
        OFFICIAL_FUZZY: "unverified (the site's name only resembles the organisation's)",
    }.get(level, "no")
    prompt = (
        f"Story: {story.strip()[:600]}\n"
        f"Cover headline: {hook.strip()}\n"
        f"{found_on}"
        f"Published by the story's own organisation: {own}\n"
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
        return _parse_verdict(text, frames, official=official)
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

