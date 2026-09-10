"""Small, self-contained logo snapshots saved with reusable designs and runs."""

from __future__ import annotations

import base64
import binascii
import io

from PIL import Image

MAX_LOGO_BYTES = 48 * 1024
MAX_LOGO_SIDE = 512


def decode_logo(value: str) -> bytes:
    """Validate a raster data URL without fetching URLs or accessing paths."""
    try:
        prefix, encoded = value.split(",", 1)
        expected = {
            "data:image/png;base64": "PNG",
            "data:image/jpeg;base64": "JPEG",
            "data:image/webp;base64": "WEBP",
        }.get(prefix)
        if expected is None:
            raise ValueError("Use a PNG, JPG or WebP logo.")
        payload = base64.b64decode(encoded, validate=True)
        if not payload or len(payload) > MAX_LOGO_BYTES:
            raise ValueError("The saved logo must be at most 48 KB.")
        with Image.open(io.BytesIO(payload)) as image:
            if image.format != expected or not (0 < image.width <= MAX_LOGO_SIDE and 0 < image.height <= MAX_LOGO_SIDE):
                raise ValueError("The saved logo must be a raster image up to 512 pixels per side.")
            image.verify()
        return payload
    except (binascii.Error, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("That logo is not a valid image.") from exc
