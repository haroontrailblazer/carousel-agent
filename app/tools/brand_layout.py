"""Deterministic footer furniture for generated slides.

Image models create the editorial content, but the brand favicon, handle,
swipe arrow, and their padding are composited here so every carousel uses
identical geometry and exact text.

Brand marks and their geometry belong to the selected design. Account identity
is a fallback only when that design does not supply its own logo or handle.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from itertools import combinations
from io import BytesIO
from pathlib import Path
from typing import Literal

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps, ImageStat

from app.schemas import CarouselDesign, SlideDesign
from app.text_spacing import advance, text_width, line_offset
from app.tools import brand_identity


SlideKind = Literal["body", "cta"]

SLIDE_WIDTH = 1080
SLIDE_HEIGHT = 1350
SAFE_LEFT = 88
SAFE_RIGHT = 88
SAFE_TOP = 76
SAFE_BOTTOM = 76

SLIDE_NUMBER_LEFT = SAFE_LEFT
SLIDE_NUMBER_TOP = SAFE_TOP
SLIDE_NUMBER_FONT_SIZE = 32

# Shared editorial headline contract. The deterministic cover compositor uses
# these values directly, while the body-slide image prompt names the same size
# and type treatment so slide 1 no longer looks like a different template.
HEADLINE_FONT_SIZE = 76
HEADLINE_MIN_FONT_SIZE = 60
HEADLINE_MAX_LINES = 3
HEADLINE_STYLE = "condensed bold grotesk"
BODY_FONT_SIZE = 36
BODY_MIN_FONT_SIZE = 30
TEXT_PANEL_TOP = 140
TEXT_PANEL_BOTTOM = 620
TEXT_CONTENT_LEFT = SAFE_LEFT
TEXT_CONTENT_RIGHT = SLIDE_WIDTH - SAFE_RIGHT

RAIL_DIVIDER_Y = 1160
RAIL_FILL_TOP = RAIL_DIVIDER_Y
RAIL_CENTER_Y = 1232
RAIL_RIGHT = SLIDE_WIDTH - SAFE_RIGHT

BODY_FAVICON_SIZE = 56
BODY_FAVICON_LEFT = SAFE_LEFT
BODY_HANDLE_LEFT = 160
BODY_ARROW_LEFT = 944

CTA_FAVICON_SIZE = BODY_FAVICON_SIZE
CTA_FAVICON_LEFT = SAFE_LEFT
CTA_HANDLE_LEFT = BODY_HANDLE_LEFT

INK = (22, 24, 17)
PAPER = (247, 247, 245)
ACCENT_GREEN = (143, 184, 50)
WARM_WHITE = (232, 228, 214)
TEXT_DARK = (26, 26, 24)
MUTED_DARK = (113, 122, 95)
MUTED_LIGHT = (185, 197, 170)


@dataclass(frozen=True)
class _TypographyLayout:
    """One measured, readable typography layout that fits the top panel."""

    head_font: ImageFont.FreeTypeFont | ImageFont.ImageFont
    body_font: ImageFont.FreeTypeFont | ImageFont.ImageFont
    headline_lines: list[str]
    wrapped_body: list[list[str]]
    headline_size: int
    body_size: int
    head_line_height: int
    body_line_height: int
    head_gap: int
    body_gap: int
    thought_gap: int
    section_gap: int
    total_height: int

# The browser and compositor read the same manifest and exact font binaries.
# Never substitute host fonts: that changes a saved design between dev and Linux.
_FONT_DIRECTORY = Path(__file__).resolve().parents[2] / "frontend/public/fonts/carousel"
_DESIGN_FONTS = json.loads((_FONT_DIRECTORY / "manifest.json").read_text(encoding="utf-8"))


def design_font(
    size: int,
    family: str = "condensed",
    *,
    bold: bool = False,
) -> ImageFont.FreeTypeFont:
    """Load the exact selected face and weight also served to the design editor."""
    face = _DESIGN_FONTS[family]
    path = _FONT_DIRECTORY / face["bold" if bold else "regular"]
    return ImageFont.truetype(str(path), size=size)


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    return design_font(size, "sans", bold=bold)


def headline_font(size: int) -> ImageFont.FreeTypeFont:
    return design_font(size, "condensed", bold=True)


def hex_color(value: str, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    """Parse a validated CSS hex token for Pillow, retaining a safe fallback."""
    text = str(value or "").lstrip("#")
    try:
        if len(text) == 6:
            return tuple(int(text[index : index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        pass
    return fallback


def _line_width(
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    text: str,
    letter_spacing: float = 0,
    word_spacing: float = 0,
) -> float:
    """Measure one line using the same font instance used for drawing."""
    return text_width(font, text, letter_spacing, word_spacing)


def _balanced_wrap(
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
    max_lines: int,
    letter_spacing: float = 0,
    word_spacing: float = 0,
) -> list[str]:
    """Wrap a short headline into balanced lines without shrinking its font."""
    normalized = " ".join(str(text or "").split())
    if not normalized:
        return []
    if _line_width(font, normalized, letter_spacing, word_spacing) <= max_width:
        return [normalized]
    words = normalized.split(" ")
    if any(_line_width(font, word, letter_spacing, word_spacing) > max_width for word in words):
        raise ValueError("headline contains a word wider than the fixed text area")
    best_overall: tuple[float, list[str]] | None = None
    for line_count in range(2, min(max_lines, len(words)) + 1):
        best: tuple[float, list[str]] | None = None
        for cuts in combinations(range(1, len(words)), line_count - 1):
            boundaries = (0, *cuts, len(words))
            lines = [
                " ".join(words[boundaries[i] : boundaries[i + 1]])
                for i in range(line_count)
            ]
            widths = [_line_width(font, line, letter_spacing, word_spacing) for line in lines]
            score = max(widths) + (max(widths) - min(widths)) * 0.12
            if best is None or score < best[0]:
                best = (score, lines)
        if best is None:
            continue
        best_overall = best
        if all(_line_width(font, line, letter_spacing, word_spacing) <= max_width for line in best[1]):
            return best[1]
    if best_overall is None or any(
        _line_width(font, line, letter_spacing, word_spacing) > max_width for line in best_overall[1]
    ):
        raise ValueError(
            f"headline does not fit at the fixed {HEADLINE_FONT_SIZE}px size "
            f"within {HEADLINE_MAX_LINES} lines"
        )
    return best_overall[1]


def _greedy_wrap(
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
    letter_spacing: float = 0,
    word_spacing: float = 0,
) -> list[str]:
    """Wrap body copy at a fixed size while preserving every word verbatim."""
    normalized = " ".join(str(text or "").split())
    if not normalized:
        return []
    words = normalized.split(" ")
    if any(_line_width(font, word, letter_spacing, word_spacing) > max_width for word in words):
        raise ValueError("body copy contains a word wider than the fixed text area")
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if _line_width(font, candidate, letter_spacing, word_spacing) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _headline_highlight(headline: str) -> str:
    """Choose one stable emphasis phrase without changing the approved copy."""
    words = " ".join(str(headline or "").split()).split(" ")
    if not words:
        return ""
    return " ".join(words[-2:]) if len(words) >= 2 else words[0]


def _fit_typography_layout(
    headline_text: str,
    clean_body: list[str],
    max_width: int,
    *,
    preferred_headline_size: int = HEADLINE_FONT_SIZE,
    font_family: str = "condensed",
    letter_spacing: float = 0,
    word_spacing: float = 0,
    line_height: int | None = None,
    panel_top: int = TEXT_PANEL_TOP,
    panel_bottom: int = TEXT_PANEL_BOTTOM,
) -> _TypographyLayout:
    """Choose the largest balanced type pair that fits without changing copy."""
    minimum_floor = (
        HEADLINE_MIN_FONT_SIZE
        if preferred_headline_size == HEADLINE_FONT_SIZE
        else 44
    )
    minimum_headline = max(minimum_floor, preferred_headline_size - 18)
    head_sizes = range(preferred_headline_size, minimum_headline - 1, -2)
    body_sizes = range(BODY_FONT_SIZE, BODY_MIN_FONT_SIZE - 1, -1)
    candidates = [
        (head_size, body_size)
        for head_size in head_sizes
        for body_size in body_sizes
    ]
    head_span = max(preferred_headline_size - minimum_headline, 1)
    body_span = max(BODY_FONT_SIZE - BODY_MIN_FONT_SIZE, 1)
    candidates.sort(
        key=lambda sizes: (
            max(
                (preferred_headline_size - sizes[0]) / head_span,
                (BODY_FONT_SIZE - sizes[1]) / body_span,
            ),
            (preferred_headline_size - sizes[0]) / head_span
            + (BODY_FONT_SIZE - sizes[1]) / body_span,
        )
    )

    for headline_size, body_size in candidates:
        head_font = design_font(headline_size, font_family, bold=True)
        body_font = design_font(body_size, font_family)
        try:
            headline_lines = _balanced_wrap(
                headline_text,
                head_font,
                max_width,
                HEADLINE_MAX_LINES, letter_spacing, word_spacing,
            )
            wrapped_body = [
                _greedy_wrap(line, body_font, max_width, letter_spacing, word_spacing) for line in clean_body
            ]
        except ValueError:
            continue

        head_ascent, head_descent = head_font.getmetrics()
        body_ascent, body_descent = body_font.getmetrics()
        head_line_height = head_ascent + head_descent
        body_line_height = body_ascent + body_descent
        head_gap = max(4, round(head_line_height * 0.06))
        body_gap = max(5, round(body_line_height * 0.14))
        if line_height is not None:
            head_line_height = round(headline_size * line_height / 100)
            body_line_height = round(body_size * line_height / 100)
            head_gap = body_gap = 0
        thought_gap = 10
        headline_height = (
            len(headline_lines) * head_line_height
            + max(0, len(headline_lines) - 1) * head_gap
        )
        body_height = sum(
            len(lines) * body_line_height + max(0, len(lines) - 1) * body_gap
            for lines in wrapped_body
        ) + max(0, len(wrapped_body) - 1) * thought_gap
        section_gap = 28 if wrapped_body else 0
        total_height = headline_height + section_gap + body_height
        if panel_top + total_height <= panel_bottom:
            return _TypographyLayout(
                head_font=head_font,
                body_font=body_font,
                headline_lines=headline_lines,
                wrapped_body=wrapped_body,
                headline_size=headline_size,
                body_size=body_size,
                head_line_height=head_line_height,
                body_line_height=body_line_height,
                head_gap=head_gap,
                body_gap=body_gap,
                thought_gap=thought_gap,
                section_gap=section_gap,
                total_height=total_height,
            )

    raise ValueError(
        "approved slide copy does not fit the typography reservation even at "
        f"the readable minimums ({minimum_headline}px headline and "
        f"{BODY_MIN_FONT_SIZE}px body); shorten the copy upstream"
    )


def apply_slide_typography(
    image: Image.Image,
    headline: str,
    body_lines: list[str],
    *,
    uppercase_headline: bool = False,
    theme: Literal["auto", "paper", "ink"] = "auto",
    design: CarouselDesign | None = None,
) -> Image.Image:
    """Composite the selected design's readable typography over a text-free visual.

    The preferred 76px/36px sizes are used whenever they fit. Longer approved
    copy steps down proportionally within the explicit 60px/30px readable
    limits, preserving every word and the shared type treatment.
    """
    result = image.convert("RGB")
    slide: SlideDesign | None = design.inside if design is not None else None
    if slide is not None:
        background = hex_color(slide.background, PAPER)
        text_color = hex_color(slide.text_color, TEXT_DARK)
        highlight_text_color = hex_color(slide.highlight_text_color, ACCENT_GREEN)
    elif theme == "paper":
        background, text_color = PAPER, TEXT_DARK
        highlight_text_color = ACCENT_GREEN
    elif theme == "ink":
        background, text_color = INK, WARM_WHITE
        highlight_text_color = ACCENT_GREEN
    else:
        background, text_color, _divider = _visual_field_colors(result)
        highlight_text_color = ACCENT_GREEN
    headline_text = " ".join(str(headline or "").split())
    if uppercase_headline:
        headline_text = headline_text.upper()
    clean_body = [" ".join(str(line).split()) for line in body_lines if str(line).strip()]

    title_transform = slide.title_transform if slide is not None else None
    if title_transform is not None:
        content_left = round(SLIDE_WIDTH * title_transform.x / 100)
        max_width = max(1, round(SLIDE_WIDTH * title_transform.width / 100))
        content_right = min(SLIDE_WIDTH, content_left + max_width)
        panel_top = round(SLIDE_HEIGHT * title_transform.y / 100)
        panel_bottom = max(
            panel_top + 160,
            round(SLIDE_HEIGHT * (title_transform.y + title_transform.height) / 100),
        )
        panel_bottom = min(panel_bottom, SLIDE_HEIGHT - slide.safe_margin)
    else:
        content_left = slide.safe_margin if slide is not None else TEXT_CONTENT_LEFT
        content_right = SLIDE_WIDTH - content_left
        panel_top = max(84, content_left) if slide is not None else TEXT_PANEL_TOP
        panel_bottom = TEXT_PANEL_BOTTOM
        max_width = content_right - content_left
    layout = _fit_typography_layout(
        headline_text,
        clean_body,
        max_width,
        preferred_headline_size=(slide.title_size if slide else HEADLINE_FONT_SIZE),
        font_family=(slide.font_family if slide else "condensed"),
        letter_spacing=(slide.letter_spacing if slide else 0),
        word_spacing=(slide.word_spacing if slide else 0),
        line_height=(slide.line_height if slide else None),
        panel_top=panel_top,
        panel_bottom=panel_bottom,
    )
    head_font = layout.head_font
    body_font = layout.body_font
    headline_lines = layout.headline_lines
    wrapped_body = layout.wrapped_body

    draw = ImageDraw.Draw(result)
    # Freeform layouts may intentionally place the visual behind or beside the
    # title. Preserve that layer instead of repainting the whole legacy panel.
    if title_transform is None:
        draw.rectangle((0, 0, SLIDE_WIDTH, TEXT_PANEL_BOTTOM), fill=background)
    highlight = _headline_highlight(headline_text)
    highlight_start = headline_text.rfind(highlight) if highlight else -1
    highlight_end = highlight_start + len(highlight) if highlight_start >= 0 else -1
    global_index = 0
    if title_transform is not None:
        y = panel_top
    elif slide is None or slide.title_position.startswith("top"):
        y = panel_top
    elif slide.title_position.startswith("middle"):
        y = panel_top + max(0, (panel_bottom - panel_top - layout.total_height) // 2)
    else:
        y = max(panel_top, panel_bottom - layout.total_height - 18)
    letter_spacing = slide.letter_spacing if slide else 0
    word_spacing = slide.word_spacing if slide else 0
    head_offset = line_offset(head_font, layout.head_line_height) if slide else 0
    body_offset = line_offset(body_font, layout.body_line_height) if slide else 0
    widest_headline = max((_line_width(head_font, line, letter_spacing, word_spacing) for line in headline_lines), default=0)
    horizontal = slide.title_position.split("-", 1)[1] if slide else "left"
    if title_transform is not None:
        block_left = float(content_left)
        alignment_width = float(max_width)
    elif horizontal == "center":
        block_left = (SLIDE_WIDTH - widest_headline) / 2
        alignment_width = float(widest_headline)
    elif horizontal == "right":
        block_left = content_right - widest_headline
        alignment_width = float(widest_headline)
    else:
        block_left = float(content_left)
        alignment_width = float(widest_headline)
    for line in headline_lines:
        line_width = _line_width(head_font, line, letter_spacing, word_spacing)
        if slide is not None and slide.title_align == "center":
            x = float(block_left + (alignment_width - line_width) / 2)
        elif slide is not None and slide.title_align == "right":
            x = float(block_left + alignment_width - line_width)
        else:
            x = float(block_left)
        for char in line:
            color = highlight_text_color if highlight_start <= global_index < highlight_end else text_color
            draw.text((x, y + head_offset), char, font=head_font, fill=color)
            x += advance(head_font, char, letter_spacing, word_spacing)
            global_index += 1
        global_index += 1
        y += layout.head_line_height + layout.head_gap
    if headline_lines:
        y -= layout.head_gap
    y += layout.section_gap
    for thought_index, lines in enumerate(wrapped_body):
        for line_index, line in enumerate(lines):
            width = _line_width(body_font, line, letter_spacing, word_spacing)
            x = float(content_left)
            if slide and slide.title_align == "center":
                x += (max_width - width) / 2
            elif slide and slide.title_align == "right":
                x += max_width - width
            for char in line:
                draw.text((x, y + body_offset), char, font=body_font, fill=text_color)
                x += advance(body_font, char, letter_spacing, word_spacing)
            y += layout.body_line_height
            if line_index < len(lines) - 1:
                y += layout.body_gap
        if thought_index < len(wrapped_body) - 1:
            y += layout.thought_gap
    return result


def draw_slide_number(
    image: Image.Image,
    slide_no: int | str,
    *,
    fill: tuple[int, int, int] = WARM_WHITE,
) -> None:
    """Draw one fixed two-digit number at the shared top-left anchor."""
    try:
        tag = f"{int(slide_no):02d}"
    except (TypeError, ValueError):
        tag = str(slide_no).strip().zfill(2)
    color: tuple[int, ...] = fill
    if image.mode == "RGBA":
        color = (*fill, 255)
    ImageDraw.Draw(image).text(
        (SLIDE_NUMBER_LEFT, SLIDE_NUMBER_TOP),
        tag,
        font=_font(SLIDE_NUMBER_FONT_SIZE, bold=True),
        fill=color,
        anchor="lt",
    )


def _clear_slide_number_zone(
    image: Image.Image,
    fill: tuple[int, int, int],
) -> None:
    """Clear the fixed number reservation before drawing the exact tag."""
    ImageDraw.Draw(image).rectangle(
        (
            SLIDE_NUMBER_LEFT - 4,
            SLIDE_NUMBER_TOP - 4,
            SLIDE_NUMBER_LEFT + 76,
            SLIDE_NUMBER_TOP + 48,
        ),
        fill=fill,
    )


def normalize_accent_green(
    image: Image.Image,
    target: tuple[int, int, int] = ACCENT_GREEN,
) -> Image.Image:
    """Lock every strong lime accent pixel to the one brand green token."""
    result = image.convert("RGB")
    pixels = list(result.getdata())
    normalized: list[tuple[int, int, int]] = []
    for red, green, blue in pixels:
        is_lime = (
            green >= 135
            and red >= 70
            and blue <= 170
            and green >= red * 1.06
            and red >= blue * 1.18
            and green - red >= 20
            and green - blue >= 55
        )
        normalized.append(target if is_lime else (red, green, blue))
    result.putdata(normalized)
    return result


def _is_light_slide(image: Image.Image) -> bool:
    """Classify a finished slide from its deterministic upper field."""
    sample = image.convert("RGB").crop((24, 180, 80, 250))
    r, g, b = ImageStat.Stat(sample).mean[:3]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b >= 145


def _visual_field_colors(image: Image.Image) -> tuple[tuple[int, int, int], ...]:
    """Infer a theme from a generated lower visual before typography exists."""
    sample = image.convert("RGB").crop(
        (0, TEXT_PANEL_BOTTOM, SLIDE_WIDTH, RAIL_DIVIDER_Y)
    )
    r, g, b = ImageStat.Stat(sample).mean[:3]
    if 0.2126 * r + 0.7152 * g + 0.0722 * b >= 145:
        return PAPER, TEXT_DARK, MUTED_DARK
    return INK, WARM_WHITE, MUTED_LIGHT


def _rail_colors(image: Image.Image) -> tuple[tuple[int, int, int], ...]:
    """Return background, text, divider colors for the slide theme."""
    if _is_light_slide(image):
        return PAPER, TEXT_DARK, MUTED_DARK
    return INK, WARM_WHITE, MUTED_LIGHT


def anchor_dominant_visual_to_divider(image: Image.Image) -> Image.Image:
    """Bottom-align a dominant visual without resizing or cropping its content."""
    result = image.convert("RGB")
    visual_top = TEXT_PANEL_BOTTOM
    visual_bottom = RAIL_DIVIDER_Y
    bottom_band = result.crop((0, visual_bottom - 8, SLIDE_WIDTH, visual_bottom))
    mean = ImageStat.Stat(bottom_band).mean[:3]
    background = tuple(round(channel) for channel in mean)
    pixels = result.load()
    dominant_rows: list[int] = []
    sampled_columns = len(range(0, SLIDE_WIDTH, 4))
    for y in range(visual_top, visual_bottom):
        changed = 0
        for x in range(0, SLIDE_WIDTH, 4):
            red, green, blue = pixels[x, y]
            delta = (
                abs(red - background[0])
                + abs(green - background[1])
                + abs(blue - background[2])
            )
            if delta >= 70:
                changed += 1
        if changed / sampled_columns >= 0.32:
            dominant_rows.append(y)
    if not dominant_rows:
        return result
    last_content = max(dominant_rows)
    if last_content >= visual_bottom - 5:
        return result
    source_height = last_content + 1 - visual_top
    if source_height < 180:
        return result
    source = result.crop((0, visual_top, SLIDE_WIDTH, last_content + 1))
    gap = visual_bottom - (last_content + 1)
    ImageDraw.Draw(result).rectangle(
        (0, visual_top, SLIDE_WIDTH, visual_bottom - 1),
        fill=background,
    )
    result.paste(source, (0, visual_top + gap))
    return result


def _draw_round_line(
    draw: ImageDraw.ImageDraw,
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    fill: tuple[int, int, int],
    width: int,
) -> None:
    """Draw one line with round caps."""
    draw.line((start, end), fill=fill, width=width)
    radius = width / 2
    for x, y in (start, end):
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=fill)


def _favicon_from_source(size: int, design: CarouselDesign | None = None) -> Image.Image:
    """The profile mark of the account this run is publishing to.

    Raises:
        brand_identity.NoBrandIdentity: when no account is in context. That is
            deliberate: there is no safe default here, because drawing some
            other account's logo would mis-brand a post that nobody would
            check before it went live.
    """
    return brand_identity.require_favicon(size, design)


def _draw_handle(
    image: Image.Image,
    handle: str,
    *,
    left: int,
    center_y: int,
    fill: tuple[int, int, int],
) -> None:
    """Draw the account's handle with exact spelling and vertical centering.

    An empty handle is an error rather than a default. The old fallback named
    one specific account, which on a console with several connected is not a
    safe guess - it is a wrong one that ships.
    """
    text = handle.strip()
    if not text:
        raise brand_identity.NoBrandIdentity(
            "The brand rail was asked to draw an empty handle."
        )
    if not text.startswith("@"):
        text = "@" + text
    font = _font(32)
    draw = ImageDraw.Draw(image)
    bbox = draw.textbbox((0, 0), text, font=font)
    height = bbox[3] - bbox[1]
    draw.text((left, center_y - height / 2 - bbox[1]), text, font=font, fill=fill)


def _position_box(
    position: str,
    width: int,
    height: int,
    *,
    margin: int,
) -> tuple[int, int]:
    """Place an editable brand element inside the slide safe area."""
    vertical, horizontal = position.split("-", 1)
    if horizontal == "left":
        left = margin
    elif horizontal == "right":
        left = SLIDE_WIDTH - margin - width
    else:
        left = (SLIDE_WIDTH - width) // 2
    if vertical == "top":
        top = margin
    elif vertical == "bottom":
        top = SLIDE_HEIGHT - margin - height
    else:
        top = (SLIDE_HEIGHT - height) // 2
    return left, top


def _transform_box(transform: object) -> tuple[int, int, int, int]:
    """The editor's percentage rectangle, including its width and height."""
    return (
        round(SLIDE_WIDTH * float(getattr(transform, "x")) / 100),
        round(SLIDE_HEIGHT * float(getattr(transform, "y")) / 100),
        max(1, round(SLIDE_WIDTH * float(getattr(transform, "width")) / 100)),
        max(1, round(SLIDE_HEIGHT * float(getattr(transform, "height")) / 100)),
    )


def _paste_mark(image: Image.Image, mark: Image.Image, xy: tuple[int, int]) -> None:
    if image.mode == "RGBA":
        image.alpha_composite(mark, xy)
    else:
        image.paste(mark, xy, mark)


def _draw_logo_positioned(image: Image.Image, design: CarouselDesign, slide: SlideDesign) -> None:
    if slide.logo_transform is None:
        mark = _favicon_from_source(design.logo_size, design)
        left, top = _position_box(design.logo_position, mark.width, mark.height, margin=slide.safe_margin)
    else:
        left, top, width, height = _transform_box(slide.logo_transform)
        # The editor uses object-fit: contain. Decode an uploaded logo directly
        # so a rectangular box does not get a second square letterbox.
        identity = brand_identity.current(design)
        if design.logo_data_url and identity is not None:
            with Image.open(BytesIO(identity.favicon_png)) as source:
                mark = ImageOps.contain(source.convert("RGBA"), (width, height), Image.Resampling.LANCZOS)
        else:
            mark = _favicon_from_source(min(width, height), design)
        left += (width - mark.width) // 2
        top += (height - mark.height) // 2
    _paste_mark(image, mark, (left, top))


def _draw_handle_positioned(
    image: Image.Image,
    handle: str,
    *,
    position: str,
    font_size: int,
    margin: int,
    fill: tuple[int, int, int],
    transform: object | None = None,
) -> None:
    text = handle.strip()
    if not text:
        raise brand_identity.NoBrandIdentity("The brand rail was asked to draw an empty handle.")
    if not text.startswith("@"):
        text = "@" + text
    font = _font(font_size, bold=True)
    draw = ImageDraw.Draw(image)
    box = draw.textbbox((0, 0), text, font=font)
    width, height = box[2] - box[0], box[3] - box[1]
    if transform is not None:
        left, top, box_width, box_height = _transform_box(transform)
        # Match the editor's left-aligned, vertically centered flex box. Clip
        # inside that box rather than moving long handles away from saved x/y.
        label = Image.new("RGBA", (box_width, box_height))
        ImageDraw.Draw(label).text(
            (-box[0], (box_height - height) / 2 - box[1]), text, font=font, fill=(*fill, 255)
        )
        _paste_mark(image, label, (left, top))
        return
    left, top = _position_box(position, width, height, margin=margin)
    draw.text((left - box[0], top - box[1]), text, font=font, fill=fill)


def draw_design_branding(
    image: Image.Image,
    handle: str,
    design: CarouselDesign,
    slide: SlideDesign,
    fill: tuple[int, int, int],
    *,
    only: Literal["logo", "handle"] | None = None,
) -> None:
    """Use the same branding layout for cover, body, CTA and deterministic QA."""
    logo_visible = design.logo_visible and slide.logo_visible
    handle_visible = design.handle_visible and slide.handle_visible and bool(handle)
    shared_anchor = (
        logo_visible and handle_visible and design.logo_position == design.handle_position
        and slide.logo_transform is None and slide.handle_transform is None
    )
    if shared_anchor:
        label = handle if handle.startswith("@") else "@" + handle
        font = _font(design.handle_size, bold=True)
        box = ImageDraw.Draw(image).textbbox((0, 0), label, font=font)
        width, height = box[2] - box[0], box[3] - box[1]
        left, top = _position_box(
            design.logo_position, design.logo_size + 16 + width,
            max(design.logo_size, height), margin=slide.safe_margin,
        )
        if only != "handle":
            _paste_mark(image, _favicon_from_source(design.logo_size, design), (left, top))
        if only != "logo":
            ImageDraw.Draw(image).text(
                (left + design.logo_size + 16 - box[0], top + (design.logo_size - height) / 2 - box[1]),
                label, font=font, fill=fill,
            )
        return
    if logo_visible and only != "handle":
        _draw_logo_positioned(image, design, slide)
    if handle_visible and only != "logo":
        _draw_handle_positioned(
            image, handle, position=design.handle_position, font_size=design.handle_size,
            margin=slide.safe_margin, fill=fill, transform=slide.handle_transform,
        )


def _draw_swipe_arrow(image: Image.Image, fill: tuple[int, int, int]) -> None:
    """Draw the icon-only right arrow inside the safe-area boundary."""
    draw = ImageDraw.Draw(image)
    y = RAIL_CENTER_Y
    width = 4
    _draw_round_line(draw, (BODY_ARROW_LEFT, y), (RAIL_RIGHT, y), fill=fill, width=width)
    _draw_round_line(
        draw,
        (RAIL_RIGHT - 18, y - 17),
        (RAIL_RIGHT, y),
        fill=fill,
        width=width,
    )
    _draw_round_line(
        draw,
        (RAIL_RIGHT - 18, y + 17),
        (RAIL_RIGHT, y),
        fill=fill,
        width=width,
    )


def _prepare_rail(
    image: Image.Image,
    design: CarouselDesign | None = None,
) -> tuple[int, int, int]:
    """Clear the rail below its divider while preserving visuals that meet it."""
    if design is not None:
        background = hex_color(design.inside.background, PAPER)
        text = hex_color(design.inside.text_color, TEXT_DARK)
        divider = tuple(round((background[index] * 3 + text[index]) / 4) for index in range(3))
    else:
        background, text, divider = _rail_colors(image)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, RAIL_FILL_TOP, SLIDE_WIDTH, SLIDE_HEIGHT), fill=background)
    draw.line((SAFE_LEFT, RAIL_DIVIDER_Y, RAIL_RIGHT, RAIL_DIVIDER_Y), fill=divider, width=2)
    return text


def apply_body_brand_rail(
    image: Image.Image,
    handle: str,
    slide_no: int | str | None = None,
    design: CarouselDesign | None = None,
) -> Image.Image:
    """Add the selected logo, exact handle, divider, and arrow."""
    handle = (design.handle_text or handle) if design else handle
    result = image.convert("RGB")
    background = hex_color(design.inside.background, PAPER) if design else _rail_colors(result)[0]
    text = _prepare_rail(result, design)
    if slide_no is not None:
        _clear_slide_number_zone(result, background)
        draw_slide_number(result, slide_no, fill=text)
    identity = brand_identity.current(design)
    if identity is not None and identity.unbranded:
        _draw_swipe_arrow(result, text)
        return result
    if design is None:
        favicon_top = round(RAIL_CENTER_Y - BODY_FAVICON_SIZE / 2)
        favicon = _favicon_from_source(BODY_FAVICON_SIZE)
        result.paste(favicon, (BODY_FAVICON_LEFT, favicon_top), favicon)
        _draw_handle(result, handle, left=BODY_HANDLE_LEFT, center_y=RAIL_CENTER_Y, fill=text)
    else:
        draw_design_branding(result, handle, design, design.inside, text)
    _draw_swipe_arrow(result, text)
    return result


def apply_cta_brand_rail(
    image: Image.Image,
    handle: str,
    design: CarouselDesign | None = None,
) -> Image.Image:
    """Add selected CTA branding to the unnumbered closing slide."""
    if design is not None:
        design = design.model_copy(update={"inside": design.cta})
    handle = (design.handle_text or handle) if design else handle
    result = image.convert("RGB")
    text = _prepare_rail(result, design)
    identity = brand_identity.current(design)
    if identity is not None and identity.unbranded:
        return result
    if design is None:
        favicon_top = round(RAIL_CENTER_Y - CTA_FAVICON_SIZE / 2)
        favicon = _favicon_from_source(CTA_FAVICON_SIZE)
        result.paste(favicon, (CTA_FAVICON_LEFT, favicon_top), favicon)
        _draw_handle(result, handle, left=CTA_HANDLE_LEFT, center_y=RAIL_CENTER_Y, fill=text)
    else:
        draw_design_branding(result, handle, design, design.cta, text)
    return result


def validate_footer_padding(
    data: bytes,
    kind: SlideKind,
    expect_slide_number: bool = False,
    *,
    design: CarouselDesign | None = None,
) -> list[str]:
    """Validate the selected branding, divider and exact native size.

    Args:
        expect_slide_number: Whether a slide number should be drawn in the
            fixed top-left anchor. Deliberately a BOOLEAN, not the number.

            This used to take the expected value, which read as though the
            check compared it - it never did, and could not: the only test
            available here is pixel variance in the anchor box, which says
            that *something* was drawn, not *what*. Passing a number the
            function ignored made the check look stronger than it is, so a
            wrong number on a slide would have been reported as verified.
            Reading which digits are rendered would need OCR; until something
            does, the honest signature is the one that admits the limit.
    """
    errors: list[str] = []
    try:
        with Image.open(BytesIO(data)) as opened:
            image = opened.convert("RGB")
    except Exception as exc:
        return [f"could not decode PNG for padding validation: {exc}"]
    if image.size != (SLIDE_WIDTH, SLIDE_HEIGHT):
        return [f"expected {SLIDE_WIDTH}x{SLIDE_HEIGHT}, got {image.width}x{image.height}"]

    divider = image.crop((SAFE_LEFT, RAIL_DIVIDER_Y - 1, RAIL_RIGHT, RAIL_DIVIDER_Y + 2))
    if len(divider.getcolors(maxcolors=100_000) or []) < 2:
        errors.append("footer divider is missing")

    # Verify the current design/account marks, never the colors or location of
    # an unrelated brand. Explicitly unbranded runs and hidden marks are valid.
    identity = brand_identity.current(design)
    if identity is not None and not identity.unbranded:
        layers: dict[str, Image.Image] = {}
        if design is not None:
            slide = design.cta if kind == "cta" else design.inside
            text = hex_color(slide.text_color, TEXT_DARK)
            for element in ("logo", "handle"):
                layer = Image.new("RGBA", image.size)
                draw_design_branding(layer, identity.at_handle, design, slide, text, only=element)
                layers[element] = layer
        else:
            text = _rail_colors(image)[1]
            logo = Image.new("RGBA", image.size)
            _paste_mark(logo, _favicon_from_source(BODY_FAVICON_SIZE),
                        (BODY_FAVICON_LEFT, round(RAIL_CENTER_Y - BODY_FAVICON_SIZE / 2)))
            layers["logo"] = logo
            handle = Image.new("RGBA", image.size)
            if identity.at_handle:
                _draw_handle(handle, identity.at_handle, left=BODY_HANDLE_LEFT, center_y=RAIL_CENTER_Y, fill=text)
            layers["handle"] = handle

        expected = Image.new("RGBA", image.size)
        for layer in layers.values():
            expected.alpha_composite(layer)
        if kind == "body":
            _draw_swipe_arrow(expected, text)
        for element, layer in layers.items():
            bounds = layer.getbbox()
            if bounds is None:
                continue  # This design intentionally omits this mark.
            # Check opaque foreground pixels; transparent edges over artwork
            # depend on the underlying image and must not trigger false rework.
            mask = layer.getchannel("A").crop(bounds).point(lambda alpha: 255 if alpha >= 250 else 0)
            checked = mask.histogram()[255]
            if not checked:
                continue
            difference = ImageChops.difference(image.crop(bounds), expected.crop(bounds).convert("RGB"))
            red, green, blue = difference.split()
            mismatch = ImageChops.lighter(ImageChops.lighter(red, green), blue).point(lambda value: 255 if value > 24 else 0)
            failed = ImageChops.multiply(mismatch, mask).histogram()[255]
            if failed / checked > 0.08:
                errors.append(f"selected {element} is missing or does not match its configured position")
    if expect_slide_number:
        number = image.crop(
            (
                SLIDE_NUMBER_LEFT,
                SLIDE_NUMBER_TOP,
                SLIDE_NUMBER_LEFT + 72,
                SLIDE_NUMBER_TOP + 48,
            )
        )
        if sum(ImageStat.Stat(number).var) < 35:
            errors.append("slide number is missing from the fixed top-left anchor")
    return errors


__all__ = [
    "ACCENT_GREEN",
    "BODY_FONT_SIZE",
    "BODY_MIN_FONT_SIZE",
    "HEADLINE_FONT_SIZE",
    "HEADLINE_MAX_LINES",
    "HEADLINE_MIN_FONT_SIZE",
    "HEADLINE_STYLE",
    "apply_body_brand_rail",
    "apply_cta_brand_rail",
    "apply_slide_typography",
    "anchor_dominant_visual_to_divider",
    "draw_slide_number",
    "design_font",
    "hex_color",
    "headline_font",
    "normalize_accent_green",
    "validate_footer_padding",
]
