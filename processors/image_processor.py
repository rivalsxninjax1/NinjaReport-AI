"""Image metadata + preview generation.

Never writes to or modifies the source path. Previews are separate derived
files. Degrades gracefully (available=False) if Pillow is not installed.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class ImageMetadata:
    available: bool
    engine: str
    width: int | None = None
    height: int | None = None
    format: str | None = None
    mode: str | None = None


def extract_image_metadata(path: Path) -> ImageMetadata:
    try:
        from PIL import Image
    except ImportError:
        return ImageMetadata(available=False, engine="unavailable")

    with Image.open(path) as img:
        return ImageMetadata(
            available=True,
            engine="pillow",
            width=img.width,
            height=img.height,
            format=img.format,
            mode=img.mode,
        )


def generate_preview(source_path: Path, dest_path: Path, max_dimension: int = 1600) -> bool:
    """Write a resized JPEG copy at dest_path. Returns False (no-op) if Pillow
    is unavailable. Never modifies source_path."""
    try:
        from PIL import Image
    except ImportError:
        return False

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source_path) as img:
        img = img.convert("RGB") if img.mode not in ("RGB", "L") else img
        img.thumbnail((max_dimension, max_dimension))
        img.save(dest_path, format="JPEG", quality=85)
    return True
