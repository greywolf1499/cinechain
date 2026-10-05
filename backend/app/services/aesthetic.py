"""Dominant poster colour for the Aesthetic Gradient (Pillow only, in-memory)."""

from __future__ import annotations

import io
import math

from PIL import Image, UnidentifiedImageError

THUMBNAIL_SIZE = (50, 50)
PALETTE_COLORS = 6

# Euclidean RGB distance runs 0 (identical) .. ~441.7 (black vs white).
MAX_RGB_DISTANCE = math.sqrt(3 * 255**2)


def extract_dominant_color(image_bytes: bytes) -> str | None:
    """The most common colour of a poster as "#rrggbb", or None if undecodable.

    The poster is shrunk to 50x50 and median-cut quantized to a handful of
    colours; the biggest bucket wins.
    """
    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            tiny = image.convert("RGB").resize(THUMBNAIL_SIZE, Image.Resampling.BILINEAR)
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return None
    quantized = tiny.quantize(colors=PALETTE_COLORS, method=Image.Quantize.MEDIANCUT)
    palette = quantized.getpalette() or []
    counts = quantized.getcolors(maxcolors=PALETTE_COLORS * 4) or []
    if not counts:
        return None
    _, index = max(counts)
    red, green, blue = palette[index * 3 : index * 3 + 3]
    return f"#{red:02x}{green:02x}{blue:02x}"


def hex_to_rgb(value: str) -> tuple[int, int, int] | None:
    text = (value or "").strip().lstrip("#")
    if len(text) != 6:
        return None
    try:
        return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
    except ValueError:
        return None


def color_distance(hex_a: str, hex_b: str) -> float | None:
    """Euclidean distance between two "#rrggbb" colours in RGB space."""
    rgb_a, rgb_b = hex_to_rgb(hex_a), hex_to_rgb(hex_b)
    if rgb_a is None or rgb_b is None:
        return None
    return math.dist(rgb_a, rgb_b)
