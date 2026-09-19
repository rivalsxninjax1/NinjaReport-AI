"""System-level dependency checks shared by the Settings page and the CLI
`doctor` command, so the two never drift out of sync."""
from __future__ import annotations

import shutil
from dataclasses import dataclass


@dataclass
class SystemDiagnostics:
    tesseract_available: bool
    pymupdf_available: bool


def check_system_dependencies() -> SystemDiagnostics:
    tesseract_available = shutil.which("tesseract") is not None
    try:
        import fitz  # noqa: F401
        pymupdf_available = True
    except ImportError:
        pymupdf_available = False
    return SystemDiagnostics(tesseract_available=tesseract_available, pymupdf_available=pymupdf_available)


def format_system_diagnostics(d: SystemDiagnostics) -> str:
    lines = [
        f"Tesseract OCR binary: {'found' if d.tesseract_available else 'NOT FOUND (install via brew/apt)'}",
        f"PyMuPDF (PDF extraction): {'installed' if d.pymupdf_available else 'NOT INSTALLED (pip install pymupdf)'}",
    ]
    return "\n".join(lines)
