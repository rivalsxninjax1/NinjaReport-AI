"""PDF processing: per-page text extraction via PyMuPDF, with an OCR fallback
for scanned (image-only) pages. Degrades gracefully if PyMuPDF is missing.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from processors.ocr_engine import OCRResult, run_ocr_on_image_bytes


@dataclass
class PDFPageResult:
    page_number: int  # 1-indexed
    text: str | None
    is_scanned: bool
    ocr: OCRResult | None = None


@dataclass
class PDFProcessResult:
    available: bool
    engine: str
    pages: list[PDFPageResult]


def process_pdf(path: Path, run_ocr_fallback: bool = True, render_dpi: int = 200) -> PDFProcessResult:
    """Extract text per page. Pages with no extractable text are treated as
    scanned and routed through OCR (if run_ocr_fallback and OCR is available).
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return PDFProcessResult(available=False, engine="unavailable", pages=[])

    pages: list[PDFPageResult] = []
    doc = fitz.open(path)
    try:
        for index, page in enumerate(doc, start=1):
            text = (page.get_text() or "").strip()
            is_scanned = len(text) == 0
            ocr_result = None

            if is_scanned and run_ocr_fallback:
                pixmap = page.get_pixmap(dpi=render_dpi)
                ocr_result = run_ocr_on_image_bytes(pixmap.tobytes("png"))

            pages.append(
                PDFPageResult(
                    page_number=index,
                    text=text or None,
                    is_scanned=is_scanned,
                    ocr=ocr_result,
                )
            )
    finally:
        doc.close()

    return PDFProcessResult(available=True, engine="pymupdf", pages=pages)
