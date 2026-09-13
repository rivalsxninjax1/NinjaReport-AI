"""Processing pipeline: routes an Evidence record to the right processor
based on its MIME type, and records results as derived artifacts.

Never reads evidence.stored_path for anything other than input — no
processor here writes back to that path.
"""
from __future__ import annotations

from pathlib import Path

from core.models import Evidence
from processors.derived_store import DerivedStore
from processors.image_processor import generate_preview
from processors.ocr_engine import run_ocr_on_image
from processors.pdf_processor import process_pdf


def process_evidence(evidence: Evidence, derived_root: Path, derived_store: DerivedStore) -> None:
    source = Path(evidence.stored_path)
    mime = evidence.mime_type or ""

    if mime.startswith("image/"):
        _process_image(evidence, source, derived_root, derived_store)
    elif mime == "application/pdf":
        _process_pdf(evidence, source, derived_store)
    # Notes and other types: nothing to derive in Phase 2.


def _process_image(evidence: Evidence, source: Path, derived_root: Path, derived_store: DerivedStore) -> None:
    preview_dir = derived_root / evidence.project_id / evidence.id
    preview_path = preview_dir / "preview.jpg"

    if generate_preview(source, preview_path):
        derived_store.add_artifact(
            project_id=evidence.project_id,
            evidence_id=evidence.id,
            artifact_type="preview",
            engine="pillow",
            content_path=str(preview_path),
        )

    ocr = run_ocr_on_image(source)
    derived_store.add_artifact(
        project_id=evidence.project_id,
        evidence_id=evidence.id,
        artifact_type="ocr_text",
        engine=ocr.engine,
        text_content=ocr.text,
        confidence=ocr.confidence,
    )


def _process_pdf(evidence: Evidence, source: Path, derived_store: DerivedStore) -> None:
    result = process_pdf(source)
    engine = result.engine if result.available else "unavailable"

    for page in result.pages:
        text = page.text
        page_engine = engine
        confidence = None

        if page.is_scanned and page.ocr is not None:
            text = page.ocr.text
            page_engine = page.ocr.engine
            confidence = page.ocr.confidence

        derived_store.add_artifact(
            project_id=evidence.project_id,
            evidence_id=evidence.id,
            artifact_type="pdf_page_text",
            engine=page_engine,
            page_number=page.page_number,
            text_content=text,
            confidence=confidence,
        )
