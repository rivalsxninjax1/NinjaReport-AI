"""OCR engine abstraction (Tesseract via pytesseract).

Every result carries engine name, confidence, and a timestamp so downstream
code can treat OCR text as unverified until reviewed (per Phase 4 rules).
Degrades gracefully when pytesseract, Pillow, or the tesseract binary are
missing — never raises for a missing dependency, only for a corrupt image.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass
class OCRResult:
    available: bool
    engine: str
    timestamp: str
    text: str | None = None
    confidence: float | None = None  # 0.0-1.0, averaged over recognized words


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _run_ocr_on_pil_image(img) -> OCRResult:
    timestamp = _now()
    try:
        import pytesseract
    except ImportError:
        return OCRResult(available=False, engine="unavailable", timestamp=timestamp)

    try:
        data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    except Exception:
        # Covers missing tesseract binary (TesseractNotFoundError) and any
        # other OCR-time failure. We degrade rather than crash the pipeline.
        return OCRResult(available=False, engine="unavailable", timestamp=timestamp)

    words = [w for w in data.get("text", []) if w.strip()]
    confidences = [int(c) for c in data.get("conf", []) if str(c) not in ("-1",)]
    avg_confidence = (sum(confidences) / len(confidences) / 100.0) if confidences else None

    return OCRResult(
        available=True,
        engine="tesseract",
        timestamp=timestamp,
        text=" ".join(words) if words else None,
        confidence=avg_confidence,
    )


def run_ocr_on_image(path: Path) -> OCRResult:
    try:
        from PIL import Image
    except ImportError:
        return OCRResult(available=False, engine="unavailable", timestamp=_now())

    with Image.open(path) as img:
        return _run_ocr_on_pil_image(img)


def run_ocr_on_image_bytes(image_bytes: bytes) -> OCRResult:
    """Used for OCR-ing a rendered PDF page (see pdf_processor)."""
    try:
        from PIL import Image
    except ImportError:
        return OCRResult(available=False, engine="unavailable", timestamp=_now())

    with Image.open(io.BytesIO(image_bytes)) as img:
        return _run_ocr_on_pil_image(img)
