"""Smart-crop suggestions for screenshots.

Heuristic, not AI-based: estimates the background color from the image
corners, finds the bounding box of content that differs from it, and
proposes a padded crop around that region. Always includes a "full image"
fallback so a low-confidence detection never forces a bad crop.

Never touches the source file — see apply_crop() for how an accepted
suggestion becomes a separate derived file.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CropSuggestion:
    x1: int
    y1: int
    x2: int
    y2: int
    reason: str
    confidence: float  # 0.0-1.0


def suggest_crops(path: Path, margin_ratio: float = 0.05) -> list[CropSuggestion]:
    try:
        from PIL import Image, ImageChops
    except ImportError:
        return []  # Pillow unavailable: no suggestions, never crash the pipeline

    with Image.open(path) as img:
        img = img.convert("RGB")
        width, height = img.size

        corner_colors = [
            img.getpixel((0, 0)),
            img.getpixel((width - 1, 0)),
            img.getpixel((0, height - 1)),
            img.getpixel((width - 1, height - 1)),
        ]
        background_color = Counter(corner_colors).most_common(1)[0][0]

        background = Image.new("RGB", img.size, background_color)
        diff = ImageChops.difference(img, background)
        bbox = diff.getbbox()

        suggestions: list[CropSuggestion] = []

        if bbox is not None:
            x1, y1, x2, y2 = bbox
            margin_x = int((x2 - x1) * margin_ratio)
            margin_y = int((y2 - y1) * margin_ratio)
            x1 = max(0, x1 - margin_x)
            y1 = max(0, y1 - margin_y)
            x2 = min(width, x2 + margin_x)
            y2 = min(height, y2 + margin_y)

            area_ratio = ((x2 - x1) * (y2 - y1)) / (width * height)
            if area_ratio < 0.98:  # meaningfully smaller than the full image
                confidence = round(max(0.0, min(1.0, 1 - area_ratio)), 2)
                suggestions.append(
                    CropSuggestion(
                        x1, y1, x2, y2,
                        reason=(
                            f"Detected content region covering {area_ratio:.0%} of the "
                            "image; cropping removes uniform background/padding."
                        ),
                        confidence=confidence,
                    )
                )

        # Always offer the uncropped original as a safe fallback. Higher
        # confidence when no tighter crop was found (nothing better to do);
        # lower confidence when a tighter crop is also on offer (uncertain
        # which one is right, so present both).
        suggestions.append(
            CropSuggestion(
                0, 0, width, height,
                reason="Full image, no crop — preserves complete context.",
                confidence=0.5 if suggestions else 0.9,
            )
        )
        return suggestions


def apply_crop(source_path: Path, coords: tuple[int, int, int, int], dest_path: Path) -> None:
    """Write a cropped copy at dest_path. Never modifies source_path."""
    from PIL import Image

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source_path) as img:
        cropped = img.crop(coords)
        cropped.convert("RGB").save(dest_path, format="JPEG", quality=90)
