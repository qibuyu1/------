from __future__ import annotations

"""Shared, reusable image blankness gate (used by both image_fetch and code_visuals).

Determines whether a raster holds enough actual visual content to be usable as an
article image. It is deliberately *not* colour-count based: a valid editorial image
may be low-colour (monochrome news photo, single-colour poster, a data table or a
screenshot) yet carry real content. The primary signal is the fraction of non-white
pixels; a very small distinct-colour count is only a weak secondary confirm, and only
when the non-white ratio is also tiny.

Pixel access uses tobytes() (numpy-free, no Image.getdata() deprecation warning in
Pillow 12).
"""

from PIL import Image

# A pixel is "non-white" when any channel falls below this luminance threshold.
# Signals a frame that is essentially a white/blank placeholder unless content exists.
_NONWHITE_CHANNEL = 235

# Primary: below this non-white ratio the frame is basically blank white.
BLANK_PRIMARY_NONWHITE = 0.02
# Secondary (confirm): tiny non-white AND almost a single colour -> treat as blank.
BLANK_SECONDARY_NONWHITE = 0.05
BLANK_SECONDARY_COLORS = 3

_DOWNSCALE = (64, 36)


def blankness(image: Image.Image) -> tuple[float, int]:
    """Return (non_white_ratio, distinct_colour_count) on a small downsample.

    Returns (-1.0, -1) if the image cannot be inspected, which callers treat as
    not blank so a decoding hiccup never discards a usable image.
    """
    try:
        small = image.convert("RGB").resize(_DOWNSCALE, Image.Resampling.LANCZOS)
        raw = small.tobytes()
        n = small.width * small.height
        colors: set[tuple[int, int, int]] = set()
        nonwhite = 0
        for i in range(n):
            r, g, b = raw[3 * i], raw[3 * i + 1], raw[3 * i + 2]
            colors.add((r, g, b))
            if r < _NONWHITE_CHANNEL or g < _NONWHITE_CHANNEL or b < _NONWHITE_CHANNEL:
                nonwhite += 1
        return nonwhite / max(1, n), len(colors)
    except Exception:
        return -1.0, -1


def is_blank_image(image: Image.Image) -> bool:
    """True when an image is effectively blank and must not be committed.

    Robust against low-colour-but-valid editorial images: a small colour count alone
    never rejects an image. Only a very low non-white ratio (or a near-uniform tiny
    frame) is treated as blank.
    """
    ratio, colors = blankness(image)
    if ratio < 0:
        return False
    if ratio < BLANK_PRIMARY_NONWHITE:
        return True
    if ratio < BLANK_SECONDARY_NONWHITE and colors <= BLANK_SECONDARY_COLORS:
        return True
    return False
