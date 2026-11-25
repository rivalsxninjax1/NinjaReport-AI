"""Evidence page: upload, browse (paginated + filtered), review, reprocess, and delete evidence."""
from __future__ import annotations

import streamlit as st

from core.models import VerificationStatus
from core.search import search_evidence
from processors.pipeline import process_evidence
from ui.common import (
    destructive_button,
    flash,
    get_stores,
    project_selector,
    resolve_display_image,
    save_uploaded_bytes,
    show_flash,
)
from ui.style import apply_theme, status_badge

st.set_page_config(page_title="Evidence — NinjaReport AI", layout="wide")
apply_theme()
stores = get_stores()
project = project_selector(stores)

st.title("Evidence")
show_flash()

if project is None:
    st.info("Create a project first (sidebar), or generate a Quick Report from the home page.")
    st.stop()

derived_root = stores.settings.data_dir / "derived"

# ---------- Upload ----------
with st.expander("Upload evidence", expanded=False):
    uploaded = st.file_uploader(
        "Screenshot, PDF, or text note",
        type=["png", "jpg", "jpeg", "pdf", "txt"],
        accept_multiple_files=True,
        help=f"Max {stores.settings.max_upload_mb} MB per file. Executable and script files are always rejected.",
    )
    evidence_type = st.selectbox("Evidence type", ["screenshot", "pdf", "note", "other"])
    if uploaded and st.button("Add to project"):
        progress = st.progress(0.0, text="Starting...")
        added, problems = 0, []
        for i, f in enumerate(uploaded):
            progress.progress(i / len(uploaded), text=f"Processing {f.name} ({i + 1}/{len(uploaded)})")
            scratch_dir = stores.settings.data_dir / "uploads_scratch"
            saved_path = save_uploaded_bytes(f.getvalue(), f.name, scratch_dir)
            try:
                evidence = stores.evidence_store.add_evidence(
                    project_id=project.id, source_path=saved_path, original_filename=f.name,
                    evidence_type=evidence_type, max_upload_mb=stores.settings.max_upload_mb,
                )
                process_evidence(evidence, derived_root=derived_root, derived_store=stores.derived_store)
                added += 1
            except ValueError as exc:
                problems.append(f"{f.name}: {exc}")
            finally:
                saved_path.unlink(missing_ok=True)
        progress.progress(1.0, text="Done.")
        message = f"Added {added} of {len(uploaded)} file(s)."
        if problems:
            message += " Skipped — " + "; ".join(problems)
        flash("success" if not problems else "warning", message)
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
if not results:
    st.info("No evidence matches. Upload some above, or loosen the filters.")

for evidence in page_items:
    with st.expander(f"{evidence.id} — {evidence.original_filename} ({evidence.verification_status})"):
        st.markdown(
            f"Verification: {status_badge(evidence.verification_status)}", unsafe_allow_html=True,
        )
        display_image = resolve_display_image(evidence, stores.derived_store)
        if display_image is not None:
            st.image(str(display_image), width=400)

        ocr_text = stores.derived_store.get_combined_text(project.id, evidence.id)
        if ocr_text:
            st.text_area("Extracted text (OCR/PDF, unverified)", ocr_text, height=100, disabled=True,
                         key=f"ocr_{evidence.id}")

        notes = st.text_area("Manual notes", evidence.manual_notes, key=f"notes_{evidence.id}")
        statuses = [s.value for s in VerificationStatus]
        new_status = st.selectbox(
            "Verification status", statuses, index=statuses.index(evidence.verification_status),
            key=f"status_{evidence.id}",
        )
        if st.button("Save", key=f"save_{evidence.id}"):
            stores.evidence_store.update_notes(project.id, evidence.id, notes)
            stores.evidence_store.set_verification_status(project.id, evidence.id, new_status)
            flash("success", f"Saved {evidence.id}.")
            st.rerun()

        if not stores.evidence_store.verify_integrity(project.id, evidence.id):
            st.error("Hash mismatch — this stored file no longer matches its recorded SHA-256!")

        st.divider()
        if st.button("Reprocess (re-run OCR / preview)", key=f"reprocess_{evidence.id}"):
            process_evidence(evidence, derived_root=derived_root, derived_store=stores.derived_store, force=True)
            flash("success", f"Reprocessed {evidence.id}.")
            st.rerun()

        if destructive_button("Delete this evidence", key=f"delete_{evidence.id}"):
            stores.evidence_store.delete_evidence(project.id, evidence.id, derived_root=derived_root)
            flash(
                "success",
                f"Deleted {evidence.id}. It was also removed from findings, attack-path nodes, and report plans.",
            )
            st.rerun()
