"""Evidence page: upload, browse (paginated + filtered), and review evidence."""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from core.models import VerificationStatus
from core.search import search_evidence
from processors.pipeline import process_evidence
from ui.common import get_stores, project_selector, save_uploaded_bytes
from ui.style import apply_theme

st.set_page_config(page_title="Evidence — NinjaReport AI", layout="wide")
apply_theme()
stores = get_stores()
project = project_selector(stores)

st.title("Evidence")

if project is None:
    st.info("Create a project first.")
    st.stop()

# ---------- Upload ----------
with st.expander("Upload evidence", expanded=False):
    uploaded = st.file_uploader(
        "Screenshot, PDF, or text note",
        type=["png", "jpg", "jpeg", "pdf", "txt"],
        accept_multiple_files=True,
    )
    evidence_type = st.selectbox("Evidence type", ["screenshot", "pdf", "note", "other"])
    if uploaded and st.button("Add to project"):
        for f in uploaded:
            scratch_dir = stores.settings.data_dir / "uploads_scratch"
            saved_path = save_uploaded_bytes(f.getvalue(), f.name, scratch_dir)
            try:
                evidence = stores.evidence_store.add_evidence(
                    project_id=project.id, source_path=saved_path, original_filename=f.name,
                    evidence_type=evidence_type, max_upload_mb=stores.settings.max_upload_mb,
                )
                process_evidence(evidence, derived_root=stores.settings.data_dir / "derived", derived_store=stores.derived_store)
                st.success(f"Added {f.name} as {evidence.id}")
            except ValueError as exc:
                st.error(f"{f.name}: {exc}")
            finally:
                saved_path.unlink(missing_ok=True)
        st.rerun()

# ---------- Filters ----------
st.subheader("Browse")
col1, col2, col3 = st.columns(3)
query = col1.text_input("Search text (filename, notes, OCR)")
status_filter = col2.selectbox("Verification status", ["Any"] + [s.value for s in VerificationStatus])
type_filter = col3.selectbox("Type", ["Any", "screenshot", "pdf", "note", "other"])

results = search_evidence(
    stores.evidence_store, stores.derived_store, project.id,
    query=query or None,
    verification_status=None if status_filter == "Any" else status_filter,
    evidence_type=None if type_filter == "Any" else type_filter,
)

PAGE_SIZE = 10
total_pages = max(1, (len(results) + PAGE_SIZE - 1) // PAGE_SIZE)
page = st.number_input("Page", min_value=1, max_value=total_pages, value=1) - 1
page_items = results[page * PAGE_SIZE: (page + 1) * PAGE_SIZE]

st.caption(f"{len(results)} item(s) — page {page + 1} of {total_pages}")

for evidence in page_items:
    with st.expander(f"{evidence.id} — {evidence.original_filename} ({evidence.verification_status})"):
        stored_path = Path(evidence.stored_path)
        if (evidence.mime_type or "").startswith("image/") and stored_path.exists():
            st.image(str(stored_path), width=400)

        artifacts = stores.derived_store.list_artifacts(project.id, evidence.id)
        ocr_text = stores.derived_store.get_combined_text(project.id, evidence.id)
        if ocr_text:
            st.text_area("Extracted text (OCR/PDF, unverified)", ocr_text, height=100, disabled=True)

        notes = st.text_area("Manual notes", evidence.manual_notes, key=f"notes_{evidence.id}")
        new_status = st.selectbox(
            "Verification status", [s.value for s in VerificationStatus],
            index=[s.value for s in VerificationStatus].index(evidence.verification_status),
            key=f"status_{evidence.id}",
        )
        if st.button("Save", key=f"save_{evidence.id}"):
            stores.evidence_store.update_notes(project.id, evidence.id, notes)
            stores.evidence_store.set_verification_status(project.id, evidence.id, new_status)
            st.success("Saved")
            st.rerun()

        if not stores.evidence_store.verify_integrity(project.id, evidence.id):
            st.error("Hash mismatch — this stored file no longer matches its recorded SHA-256!")
