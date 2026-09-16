"""Exports page: compile the report plan and generate DOCX/PDF."""
from __future__ import annotations

from datetime import UTC, datetime

import streamlit as st

from generators.docx_generator import generate_docx
from generators.pdf_generator import generate_pdf
from generators.report_compiler import ReportCompilationError, compile_report
from ui.common import get_stores, project_selector
from ui.style import apply_theme

st.set_page_config(page_title="Exports — NinjaReport AI", layout="wide")
apply_theme()
stores = get_stores()
project = project_selector(stores)

st.title("Exports")

if project is None:
    st.info("Create a project first.")
    st.stop()

plan = stores.report_store.get_plan(project.id)
if plan is None:
    st.info("Build a report plan first, on the Report Builder page.")
    st.stop()

if st.button("Generate DOCX + PDF"):
    try:
        compiled = compile_report(project.name, plan, stores.evidence_store, stores.findings_store)
    except ReportCompilationError as exc:
        st.error(f"Cannot generate report: {exc}")
    else:
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        export_dir = stores.settings.export_dir / project.id
        docx_path = generate_docx(compiled, export_dir / f"report_{timestamp}.docx")
        pdf_path = generate_pdf(compiled, export_dir / f"report_{timestamp}.pdf")
        st.success("Generated both formats.")
        st.session_state["last_export"] = {"docx": docx_path, "pdf": pdf_path}

last_export = st.session_state.get("last_export")
if last_export:
    col1, col2 = st.columns(2)
    with open(last_export["docx"], "rb") as f:
        col1.download_button("Download DOCX", f.read(), file_name=last_export["docx"].name)
    with open(last_export["pdf"], "rb") as f:
        col2.download_button("Download PDF", f.read(), file_name=last_export["pdf"].name)

st.divider()
st.subheader("Past exports")
export_dir = stores.settings.export_dir / project.id
if export_dir.exists():
    files = sorted(export_dir.glob("*"), reverse=True)
    for f in files:
        st.write(f"{f.name} — {f.stat().st_size / 1024:.1f} KB")
else:
    st.caption("No exports yet.")
