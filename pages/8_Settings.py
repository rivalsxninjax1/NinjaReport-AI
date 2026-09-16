"""Settings page. Read-only diagnostics — .env itself is edited by hand,
never through the UI, to keep secrets/config out of any web-exposed form.
"""
from __future__ import annotations

import shutil

import streamlit as st

from ai.diagnostics import format_diagnostics, run_diagnostics
from ui.common import get_stores
from ui.style import apply_theme

st.set_page_config(page_title="Settings — NinjaReport AI", layout="wide")
apply_theme()
stores = get_stores()

st.title("Settings")

st.subheader("Data locations")
st.code(
    f"Data directory:   {stores.settings.data_dir}\n"
    f"Export directory: {stores.settings.export_dir}\n"
    f"Database:         {stores.settings.db_path()}"
)

st.subheader("AI (Ollama)")
diagnostics = run_diagnostics(
    stores.ollama, stores.settings.ollama_text_model, stores.settings.ollama_vision_model,
)
st.code(format_diagnostics(diagnostics))
if st.button("Re-check"):
    st.rerun()

st.subheader("OCR / PDF")
tesseract_available = shutil.which("tesseract") is not None
try:
    import fitz  # noqa: F401
    pymupdf_available = True
except ImportError:
    pymupdf_available = False

st.write(f"Tesseract OCR binary: {'✅ found' if tesseract_available else '❌ not found (brew/apt install tesseract)'}")
st.write(f"PyMuPDF (PDF extraction): {'✅ installed' if pymupdf_available else '❌ not installed (pip install pymupdf)'}")
st.caption(
    f"OCR_ENABLED={stores.settings.ocr_enabled}, "
    f"PDF_EXPORT_ENABLED={stores.settings.pdf_export_enabled}, "
    f"MAX_UPLOAD_MB={stores.settings.max_upload_mb}"
)

st.info("To change models or limits, edit .env in the project root and restart the app.")
