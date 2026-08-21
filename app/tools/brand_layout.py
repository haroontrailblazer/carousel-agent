"""Deterministic Baskaran Builds footer furniture for generated slides.

Image models create the editorial content, but the brand favicon, handle, CTA
avatar, swipe arrow, and their padding are composited here so every carousel
uses identical geometry and exact text.
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

RAIL_FILL_TOP = 1136
RAIL_DIVIDER_Y = 1160
RAIL_CENTER_Y = 1232
RAIL_RIGHT = SLIDE_WIDTH - SAFE_RIGHT

BODY_FAVICON_SIZE = 56
BODY_FAVICON_LEFT = SAFE_LEFT
BODY_HANDLE_LEFT = 160
BODY_ARROW_LEFT = 944

CTA_AVATAR_SIZE = 76
CTA_AVATAR_LEFT = SAFE_LEFT
CTA_HANDLE_LEFT = 182

INK = (22, 24, 17)
PAPER = (247, 247, 245)
LIME = (184, 239, 67)
WARM_WHITE = (232, 228, 214)
TEXT_DARK = (26, 26, 24)
MUTED_DARK = (113, 122, 95)
MUTED_LIGHT = (185, 197, 170)

_FONT_CANDIDATES = (
    Path("C:/Windows/Fonts/segoeui.ttf"),
    Path("C:/Windows/Fonts/arial.ttf"),
)
_OFFICIAL_FAVICON = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "references"
    / "baskaranbuilds-favicon.png"
)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a stable UI font available on Windows, with a Pillow fallback."""
    for path in _FONT_CANDIDATES:
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


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
    """Clear the reserved footer and draw its divider."""
    background, text, divider = _rail_colors(image)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, RAIL_FILL_TOP, SLIDE_WIDTH, SLIDE_HEIGHT), fill=background)
    draw.line((SAFE_LEFT, RAIL_DIVIDER_Y, RAIL_RIGHT, RAIL_DIVIDER_Y), fill=divider, width=2)
    return text


def apply_body_brand_rail(image: Image.Image, handle: str) -> Image.Image:
    """Add the official favicon, exact handle, divider, and arrow."""
    result = image.convert("RGB")
    text = _prepare_rail(result)
    favicon_top = round(RAIL_CENTER_Y - BODY_FAVICON_SIZE / 2)
    favicon = _favicon_from_source(BODY_FAVICON_SIZE)
    result.paste(favicon, (BODY_FAVICON_LEFT, favicon_top), favicon)
    _draw_handle(result, handle, left=BODY_HANDLE_LEFT, center_y=RAIL_CENTER_Y, fill=text)
    _draw_swipe_arrow(result, text)
    return result


def _avatar_from_source(path: Path, size: int) -> Image.Image:
    """Create a circular, face-focused avatar from the exact supplied photo."""
    if not path.is_file():
        raise FileNotFoundError(f"CTA profile image not found: {path}")
    with Image.open(path) as source:
        portrait = source.convert("RGB")
    side = round(min(portrait.size) * 0.48)
    center_x = portrait.width / 2
    center_y = portrait.height * 0.32
    left = round(center_x - side / 2)
    top = round(center_y - side / 2)
    left = min(max(left, 0), portrait.width - side)
    top = min(max(top, 0), portrait.height - side)
    crop = portrait.crop((left, top, left + side, top + side))
    crop = crop.resize((size, size), Image.Resampling.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    crop.putalpha(mask)
    return crop


def apply_cta_brand_rail(
    image: Image.Image,
    handle: str,
    profile_image: Path,
) -> Image.Image:
    """Add the supplied face avatar beside the exact handle on the CTA slide."""
    result = image.convert("RGB")
    text = _prepare_rail(result)
    avatar_top = round(RAIL_CENTER_Y - CTA_AVATAR_SIZE / 2)
    avatar = _avatar_from_source(profile_image, CTA_AVATAR_SIZE)
    ring_size = CTA_AVATAR_SIZE + 8
    ring = Image.new("RGBA", (ring_size, ring_size), (0, 0, 0, 0))
    ImageDraw.Draw(ring).ellipse((0, 0, ring_size - 1, ring_size - 1), fill=(*LIME, 255))
    ring.alpha_composite(avatar, (4, 4))
    result.paste(ring, (CTA_AVATAR_LEFT - 4, avatar_top - 4), ring)
    _draw_handle(result, handle, left=CTA_HANDLE_LEFT, center_y=RAIL_CENTER_Y, fill=text)
    return result


def validate_footer_padding(data: bytes, kind: SlideKind) -> list[str]:
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
        and RAIL_CENTER_Y + max(BODY_FAVICON_SIZE, CTA_AVATAR_SIZE) / 2
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
        top = round(RAIL_CENTER_Y - CTA_AVATAR_SIZE / 2)
        avatar = image.crop(
            (CTA_AVATAR_LEFT, top, CTA_AVATAR_LEFT + CTA_AVATAR_SIZE, top + CTA_AVATAR_SIZE)
        )
        if sum(ImageStat.Stat(avatar).var) < 350:
            errors.append("CTA face avatar is missing or visually empty")
    return errors


__all__ = [
    "apply_body_brand_rail",
    "apply_cta_brand_rail",
    "validate_footer_padding",
]
