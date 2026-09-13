"""Phase 2 acceptance tests: file processing + OCR.

Degradation tests force ImportError deterministically via sys.modules
injection, so they run the same regardless of what's installed. The
"real" tests use pytest.importorskip so they run fully once Pillow /
PyMuPDF / pytesseract + the tesseract binary are present, and skip
cleanly otherwise.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from core.db import get_connection, init_schema
from core.evidence_store import EvidenceStore
from processors.derived_store import DerivedStore
from processors.image_processor import extract_image_metadata, generate_preview
from processors.ocr_engine import run_ocr_on_image
from processors.pdf_processor import process_pdf
from processors.pipeline import process_evidence


def _tesseract_binary_available() -> bool:
    return shutil.which("tesseract") is not None


# ---------- Graceful degradation (deterministic, no env dependency) ----------

def test_image_metadata_degrades_without_pillow(monkeypatch, tmp_path):
    monkeypatch.setitem(__import__("sys").modules, "PIL", None)
    fake_image = tmp_path / "fake.png"
    fake_image.write_bytes(b"not really an image")
    meta = extract_image_metadata(fake_image)
    assert meta.available is False
    assert meta.engine == "unavailable"


def test_generate_preview_degrades_without_pillow(monkeypatch, tmp_path):
    monkeypatch.setitem(__import__("sys").modules, "PIL", None)
    src = tmp_path / "fake.png"
    src.write_bytes(b"not really an image")
    dest = tmp_path / "preview.jpg"
    created = generate_preview(src, dest)
    assert created is False
    assert not dest.exists()


def test_ocr_degrades_without_pytesseract(monkeypatch, tmp_path):
    monkeypatch.setitem(__import__("sys").modules, "pytesseract", None)
    src = tmp_path / "fake.png"
    src.write_bytes(b"not really an image")
    # PIL.Image.open will fail on garbage bytes before OCR is even reached
    # in some paths, so use a real tiny image for this one.
    pytest.importorskip("PIL")
    from PIL import Image

    Image.new("RGB", (10, 10), "white").save(src)
    result = run_ocr_on_image(src)
    assert result.available is False
    assert result.engine == "unavailable"
    assert result.text is None


def test_pdf_processing_degrades_without_pymupdf(monkeypatch, tmp_path):
    monkeypatch.setitem(__import__("sys").modules, "fitz", None)
    fake_pdf = tmp_path / "fake.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 not a real pdf")
    result = process_pdf(fake_pdf)
    assert result.available is False
    assert result.engine == "unavailable"
    assert result.pages == []


# ---------- Real image + OCR pipeline (skips if deps genuinely missing) ----------

def test_extract_image_metadata_real(tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image

    img_path = tmp_path / "shot.png"
    Image.new("RGB", (640, 480), "blue").save(img_path)

    meta = extract_image_metadata(img_path)
    assert meta.available is True
    assert meta.engine == "pillow"
    assert meta.width == 640
    assert meta.height == 480


def test_generate_preview_resizes_and_leaves_original_untouched(tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image

    from core.hashing import sha256_file

    src = tmp_path / "big.png"
    Image.new("RGB", (3000, 2000), "red").save(src)
    original_hash = sha256_file(src)

    dest = tmp_path / "preview.jpg"
    created = generate_preview(src, dest, max_dimension=800)
    assert created is True
    assert dest.exists()

    with Image.open(dest) as preview:
        assert max(preview.size) <= 800

    assert sha256_file(src) == original_hash  # original untouched


def test_ocr_returns_confidence_engine_timestamp(tmp_path):
    pytest.importorskip("PIL")
    pytest.importorskip("pytesseract")
    if not _tesseract_binary_available():
        pytest.skip("tesseract binary not installed on this system")

    from PIL import Image, ImageDraw

    img_path = tmp_path / "text.png"
    img = Image.new("RGB", (300, 80), "white")
    draw = ImageDraw.Draw(img)
    draw.text((10, 25), "OPEN PORT 22 SSH", fill="black")
    img.save(img_path)

    result = run_ocr_on_image(img_path)
    assert result.available is True
    assert result.engine == "tesseract"
    assert result.timestamp  # ISO timestamp present
    assert result.text is not None
    assert "22" in result.text or "SSH" in result.text.upper()
    assert result.confidence is None or 0.0 <= result.confidence <= 1.0


# ---------- PDF (skips here — PyMuPDF unavailable; runs once installed) ----------

def test_pdf_text_extraction_real(tmp_path):
    fitz = pytest.importorskip("fitz")

    pdf_path = tmp_path / "report.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Recon findings: port 443 open")
    doc.save(pdf_path)
    doc.close()

    result = process_pdf(pdf_path)
    assert result.available is True
    assert result.engine == "pymupdf"
    assert len(result.pages) == 1
    assert result.pages[0].is_scanned is False
    assert "443" in result.pages[0].text


def test_pdf_scanned_page_routes_through_ocr(tmp_path):
    fitz = pytest.importorskip("fitz")
    pytest.importorskip("pytesseract")
    if not _tesseract_binary_available():
        pytest.skip("tesseract binary not installed on this system")

    # A page with only an inserted image (no text layer) simulates a scan.
    pytest.importorskip("PIL")
    from PIL import Image

    img_bytes_path = tmp_path / "scan_source.png"
    Image.new("RGB", (400, 100), "white").save(img_bytes_path)

    pdf_path = tmp_path / "scanned.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_image(page.rect, filename=str(img_bytes_path))
    doc.save(pdf_path)
    doc.close()

    result = process_pdf(pdf_path)
    assert result.pages[0].is_scanned is True
    assert result.pages[0].ocr is not None
    assert result.pages[0].ocr.engine in ("tesseract", "unavailable")


# ---------- End-to-end pipeline + originals immutability ----------

@pytest.fixture
def stores(tmp_path):
    conn = get_connection(tmp_path / "db" / "test.sqlite3")
    init_schema(conn)
    evidence_store = EvidenceStore(conn, evidence_root=tmp_path / "evidence")
    derived_store = DerivedStore(conn)
    yield evidence_store, derived_store, tmp_path
    conn.close()


def test_pipeline_leaves_original_evidence_untouched_and_readonly(stores):
    pytest.importorskip("PIL")
    from PIL import Image

    evidence_store, derived_store, tmp_path = stores
    project = evidence_store.create_project("Phase 2 Test")

    src = tmp_path / "incoming.png"
    Image.new("RGB", (200, 200), "green").save(src)

    ev = evidence_store.add_evidence(
        project_id=project.id, source_path=src, original_filename="incoming.png",
        evidence_type="screenshot", max_upload_mb=50,
    )
    assert evidence_store.verify_integrity(project.id, ev.id) is True

    process_evidence(ev, derived_root=tmp_path / "derived", derived_store=derived_store)

    # Original must still verify and remain read-only after processing.
    assert evidence_store.verify_integrity(project.id, ev.id) is True
    mode = Path(ev.stored_path).stat().st_mode
    assert not (mode & 0o222)


def test_pipeline_records_derived_artifacts_for_image(stores):
    pytest.importorskip("PIL")
    from PIL import Image

    evidence_store, derived_store, tmp_path = stores
    project = evidence_store.create_project("Phase 2 Test")

    src = tmp_path / "incoming.png"
    Image.new("RGB", (200, 200), "green").save(src)

    ev = evidence_store.add_evidence(
        project_id=project.id, source_path=src, original_filename="incoming.png",
        evidence_type="screenshot", max_upload_mb=50,
    )

    process_evidence(ev, derived_root=tmp_path / "derived", derived_store=derived_store)

    artifacts = derived_store.list_artifacts(project.id, ev.id)
    types = {a.artifact_type for a in artifacts}
    assert "preview" in types
    assert "ocr_text" in types
    for artifact in artifacts:
        assert artifact.engine  # never blank
        assert artifact.created_at  # timestamp always recorded
