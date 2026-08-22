"""Deterministic Baskaran Builds footer furniture for generated slides.

Image models create the editorial content, but the brand favicon, handle,
swipe arrow, and their padding are composited here so every carousel uses
identical geometry and exact text.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageFont, ImageStat


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
ACCENT_GREEN = (184, 239, 67)
WARM_WHITE = (232, 228, 214)
TEXT_DARK = (26, 26, 24)
MUTED_DARK = (113, 122, 95)
MUTED_LIGHT = (185, 197, 170)

_FONT_CANDIDATES = (
    Path("C:/Windows/Fonts/segoeui.ttf"),
    Path("C:/Windows/Fonts/arial.ttf"),
)
_BOLD_FONT_CANDIDATES = (
    Path("C:/Windows/Fonts/seguisb.ttf"),
    Path("C:/Windows/Fonts/arialbd.ttf"),
)
_HEADLINE_FONT_CANDIDATES = (
    Path("C:/Windows/Fonts/bahnschrift.ttf"),
    Path("C:/Windows/Fonts/impact.ttf"),
    Path("C:/Windows/Fonts/arialbd.ttf"),
)
_OFFICIAL_FAVICON = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "references"
    / "baskaranbuilds-favicon.png"
)


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a stable UI font available on Windows, with a Pillow fallback."""
    candidates = _BOLD_FONT_CANDIDATES if bold else _FONT_CANDIDATES
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def headline_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load the shared condensed headline face used by the cover system."""
    for path in _HEADLINE_FONT_CANDIDATES:
        if not path.exists():
            continue
        try:
            font = ImageFont.truetype(str(path), size=size)
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
                    lowered = str(name).lower()
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
        return ImageFont.truetype("DejaVuSans-Bold.ttf", size=size)
    except OSError:
        return ImageFont.load_default(size=size)


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


def normalize_accent_green(image: Image.Image) -> Image.Image:
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
        normalized.append(ACCENT_GREEN if is_lime else (red, green, blue))
    result.putdata(normalized)
    return result


def _is_light_slide(image: Image.Image) -> bool:
    """Classify the slide theme from the reserved footer area."""
    sample = image.convert("RGB").crop((24, 1260, 64, 1300))
    r, g, b = ImageStat.Stat(sample).mean[:3]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b >= 145


def _rail_colors(image: Image.Image) -> tuple[tuple[int, int, int], ...]:
    """Return background, text, divider colors for the slide theme."""
    if _is_light_slide(image):
        return PAPER, TEXT_DARK, MUTED_DARK
    return INK, WARM_WHITE, MUTED_LIGHT


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


def _favicon_from_source(size: int) -> Image.Image:
    """Load the exact favicon served by baskaranbuilds.com."""
    if not _OFFICIAL_FAVICON.is_file():
        raise FileNotFoundError(f"Baskaran Builds favicon not found: {_OFFICIAL_FAVICON}")
    with Image.open(_OFFICIAL_FAVICON) as source:
        favicon = source.convert("RGBA")
    return favicon.resize((size, size), Image.Resampling.LANCZOS)


def _draw_handle(
    image: Image.Image,
    handle: str,
    *,
    left: int,
    center_y: int,
    fill: tuple[int, int, int],
) -> None:
    """Draw the configured handle with exact spelling and vertical centering."""
    text = handle.strip() or "@baskaranbuilds"
    if not text.startswith("@"):
        text = "@" + text
    font = _font(32)
    draw = ImageDraw.Draw(image)
    bbox = draw.textbbox((0, 0), text, font=font)
    height = bbox[3] - bbox[1]
    draw.text((left, center_y - height / 2 - bbox[1]), text, font=font, fill=fill)


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


def _prepare_rail(image: Image.Image) -> tuple[int, int, int]:
    """Clear the rail below its divider while preserving visuals that meet it."""
    background, text, divider = _rail_colors(image)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, RAIL_FILL_TOP, SLIDE_WIDTH, SLIDE_HEIGHT), fill=background)
    draw.line((SAFE_LEFT, RAIL_DIVIDER_Y, RAIL_RIGHT, RAIL_DIVIDER_Y), fill=divider, width=2)
    return text


def apply_body_brand_rail(
    image: Image.Image,
    handle: str,
    slide_no: int | str | None = None,
) -> Image.Image:
    """Add the official favicon, exact handle, divider, and arrow."""
    result = image.convert("RGB")
    background, _, _ = _rail_colors(result)
    text = _prepare_rail(result)
    if slide_no is not None:
        _clear_slide_number_zone(result, background)
        draw_slide_number(result, slide_no, fill=text)
    favicon_top = round(RAIL_CENTER_Y - BODY_FAVICON_SIZE / 2)
    favicon = _favicon_from_source(BODY_FAVICON_SIZE)
    result.paste(favicon, (BODY_FAVICON_LEFT, favicon_top), favicon)
    _draw_handle(result, handle, left=BODY_HANDLE_LEFT, center_y=RAIL_CENTER_Y, fill=text)
    _draw_swipe_arrow(result, text)
    return result


def apply_cta_brand_rail(
    image: Image.Image,
    handle: str,
    slide_no: int | str | None = None,
) -> Image.Image:
    """Add only the official favicon and handle to the CTA rail."""
    result = image.convert("RGB")
    background, _, _ = _rail_colors(result)
    text = _prepare_rail(result)
    if slide_no is not None:
        _clear_slide_number_zone(result, background)
        draw_slide_number(result, slide_no, fill=text)
    favicon_top = round(RAIL_CENTER_Y - CTA_FAVICON_SIZE / 2)
    favicon = _favicon_from_source(CTA_FAVICON_SIZE)
    result.paste(favicon, (CTA_FAVICON_LEFT, favicon_top), favicon)
    _draw_handle(result, handle, left=CTA_HANDLE_LEFT, center_y=RAIL_CENTER_Y, fill=text)
    return result


def validate_footer_padding(
    data: bytes,
    kind: SlideKind,
    slide_no: int | None = None,
) -> list[str]:
    """Validate footer furniture, safe-area geometry, and exact native size."""
    errors: list[str] = []
    try:
        with Image.open(BytesIO(data)) as opened:
            image = opened.convert("RGB")
    except Exception as exc:
        return [f"could not decode PNG for padding validation: {exc}"]
    if image.size != (SLIDE_WIDTH, SLIDE_HEIGHT):
        return [f"expected {SLIDE_WIDTH}x{SLIDE_HEIGHT}, got {image.width}x{image.height}"]

    if not (
        BODY_FAVICON_LEFT >= SAFE_LEFT
        and RAIL_RIGHT <= SLIDE_WIDTH - SAFE_RIGHT
        and RAIL_CENTER_Y + BODY_FAVICON_SIZE / 2
        <= SLIDE_HEIGHT - SAFE_BOTTOM
    ):
        errors.append("footer furniture falls outside the 88/76 px safe area")

    divider = image.crop((SAFE_LEFT, RAIL_DIVIDER_Y - 1, RAIL_RIGHT, RAIL_DIVIDER_Y + 2))
    if len(divider.getcolors(maxcolors=100_000) or []) < 2:
        errors.append("footer divider is missing")

    if kind == "body":
        favicon_top = round(RAIL_CENTER_Y - BODY_FAVICON_SIZE / 2)
        favicon = image.crop(
            (
                BODY_FAVICON_LEFT,
                favicon_top,
                BODY_FAVICON_LEFT + BODY_FAVICON_SIZE,
                favicon_top + BODY_FAVICON_SIZE,
            )
        )
        cream_pixels = sum(
            1
            for r, g, b in favicon.getdata()
            if r > 205 and g > 205 and b > 185
        )
        if cream_pixels < 500 or sum(ImageStat.Stat(favicon).var) < 500:
            errors.append("official Baskaran Builds favicon is missing or mispositioned")
    else:
        favicon_top = round(RAIL_CENTER_Y - CTA_FAVICON_SIZE / 2)
        favicon = image.crop(
            (
                CTA_FAVICON_LEFT,
                favicon_top,
                CTA_FAVICON_LEFT + CTA_FAVICON_SIZE,
                favicon_top + CTA_FAVICON_SIZE,
            )
        )
        cream_pixels = sum(
            1
            for r, g, b in favicon.getdata()
            if r > 205 and g > 205 and b > 185
        )
        if cream_pixels < 500 or sum(ImageStat.Stat(favicon).var) < 500:
            errors.append("official Baskaran Builds favicon is missing from the CTA rail")
    if slide_no is not None:
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
    "HEADLINE_FONT_SIZE",
    "HEADLINE_MAX_LINES",
    "HEADLINE_MIN_FONT_SIZE",
    "HEADLINE_STYLE",
    "apply_body_brand_rail",
    "apply_cta_brand_rail",
    "draw_slide_number",
    "headline_font",
    "normalize_accent_green",
    "validate_footer_padding",
]
