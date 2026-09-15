"""Shared image-fit math used by both the DOCX and PDF generators.

The single invariant this module exists to guarantee: an image is never
stretched (aspect ratio is always preserved) and never upscaled beyond its
native resolution — only ever scaled down to fit the page.
"""
from __future__ import annotations

from dataclasses import dataclass

DEFAULT_DPI = 96  # standard baseline for pixel-to-inch conversion


@dataclass
class DisplaySize:
    width_in: float
    height_in: float


def compute_display_size(
    native_width_px: int, native_height_px: int, max_width_in: float, dpi: int = DEFAULT_DPI,
) -> DisplaySize:
    """Return (width_in, height_in) for displaying an image.

    - Aspect ratio is always preserved (single scale factor for both axes).
    - Never upscaled: if the native image is smaller than max_width_in at
      `dpi`, its native size is used as-is rather than stretching it larger.
    - Never wider than max_width_in.
    - Degenerate input (zero/negative dimensions) falls back to a safe
      square placeholder at max_width_in, rather than dividing by zero.
    """
    if native_width_px <= 0 or native_height_px <= 0:
        return DisplaySize(width_in=max_width_in, height_in=max_width_in)

    native_width_in = native_width_px / dpi
    native_height_in = native_height_px / dpi

    display_width_in = min(native_width_in, max_width_in)
    scale = display_width_in / native_width_in
    display_height_in = native_height_in * scale

    return DisplaySize(width_in=display_width_in, height_in=display_height_in)
