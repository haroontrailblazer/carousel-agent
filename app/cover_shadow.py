"""Black cover mask; keep the geometry in sync with lib/cover-shadow.ts."""
from math import floor
from PIL import Image


def cover_shadow_mask(width: int, height: int, *, coverage: int = 52,
                      blur: int = 65, curve: int = 0) -> Image.Image:
    """Fade into black, with optional raised, rounded edges on either side.

    All controls are proportional to the canvas. Zero blur is a hard edge;
    zero curve is straight. The final row always remains fully opaque.
    """
    top = height * (1 - coverage / 100)
    fade = min(height * coverage / 100, height * .18 * blur / 65)
    lift = height * coverage / 100 * .35 * curve / 100
    mask = Image.new("L", (width, height))
    columns = {}
    for x in range(width):
        edge = (2 * x / max(1, width - 1) - 1) ** 2
        start = floor(top - lift * edge + .5)
        end = min(height - 1, floor(top + fade - lift * edge + .5))
        key = (start, end)
        if key not in columns:
            pixels = bytes(
                255 if y >= end else 0 if y <= start
                else floor(255 * (y - start) / max(1, end - start) + .5)
                for y in range(height)
            )
            columns[key] = Image.frombytes("L", (1, height), pixels)
        mask.paste(columns[key], (x, 0))
    return mask
