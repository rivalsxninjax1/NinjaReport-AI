"""NinjaReport AI — Quick Report (Streamlit entry point).

The default landing screen: paste text and/or drop files, pick a
template and output format, and get a generated report in one step.
Ingestion, OCR, AI analysis, finding synthesis, narrative writing, and
report assembly are all existing pieces (Phases 1-8) — this page just
orchestrates them via core.quick_report.run_quick_report().

The step-by-step pages (Projects, Evidence, Analysis, Findings, Attack
Path, Report Builder, Exports, Settings) remain fully available in the
sidebar for reviewing or refining anything Quick Report produced.
"""
from __future__ import annotations

import sys

from config import get_settings
from core.logging_setup import configure_logging


def bootstrap():
    """Load settings, ensure directories exist, configure logging, and
    clean up any orphaned scratch files from a previous crashed/interrupted
    run. Kept dependency-free (no streamlit import) so it's unit testable."""
    settings = get_settings()
    settings.ensure_directories()
    logger = configure_logging(settings.data_dir)

    from core.cleanup import clean_scratch_dir
    removed = clean_scratch_dir(settings.data_dir / "uploads_scratch")
    if removed:
        logger.info("Cleaned up %d orphaned scratch file(s) from a previous run.", removed)

    logger.info("NinjaReport AI starting up. data_dir=%s", settings.data_dir)
    return settings


def main() -> None:
    bootstrap()

    try:
        import streamlit as st
    except ImportError:
        print("Streamlit is not installed. Run: pip install -r requirements.txt", file=sys.stderr)
        sys.exit(1)

    from ai.diagnostics import run_diagnostics
    from core.quick_report import run_quick_report
    from ui.common import get_stores, project_selector
    from ui.style import apply_theme, severity_badge

    st.set_page_config(page_title="NinjaReport AI — Quick Report", layout="wide")
    apply_theme()

    stores = get_stores()
    st.title("NinjaReport AI")
    st.caption("Paste anything, drop your files, get a report.")

    sidebar_project = project_selector(stores)

    diagnostics = run_diagnostics(
        stores.ollama, stores.settings.ollama_text_model, stores.settings.ollama_vision_model,
    )
    if not diagnostics.reachable:
        st.warning(
            "Ollama is offline — the report will still generate, but without AI analysis, "
            "drafted findings, or written narrative sections (evidence will still be included)."
        )
    elif not diagnostics.text_model_ready:
        st.warning(
            f"Text model '{stores.settings.ollama_text_model or '(unset)'}' isn't ready — "
            "set OLLAMA_TEXT_MODEL in .env for AI analysis and findings."
        )

    # ---------- Quick Report form ----------
    st.subheader("New Quick Report")

    pasted_text = st.text_area(
        "Paste anything — command output, notes, a chat log",
        height=150, placeholder="e.g. nmap -sV 10.0.0.5 output, a terminal session, investigation notes...",
    )
    uploaded = st.file_uploader(
        "Or drop files — screenshots, PDFs, text files",
        type=["png", "jpg", "jpeg", "pdf", "txt"],
        accept_multiple_files=True,
    )

    col1, col2, col3 = st.columns(3)
    template_type = col1.selectbox("Report type", ["ctf", "vapt"], format_func=str.upper)
    output_format = col2.selectbox("Output format", ["pdf", "docx", "md"], format_func=str.upper)

    existing_projects = stores.evidence_store.list_projects()
    project_choice_labels = ["New project"] + [f"{p.name} ({p.id})" for p in existing_projects]
    project_choice = col3.selectbox("Add to", project_choice_labels)

    has_input = bool((pasted_text or "").strip()) or bool(uploaded)
    generate_clicked = st.button("Generate Report", type="primary", disabled=not has_input)

    if not has_input:
        st.caption("Paste some text or upload at least one file to enable report generation.")

    if generate_clicked:
        target_project_id = None
        if project_choice != "New project":
            target_project_id = existing_projects[project_choice_labels.index(project_choice) - 1].id

        uploaded_files = [(f.getvalue(), f.name) for f in (uploaded or [])]

        with st.spinner("Ingesting, analyzing, and building your report..."):
            result = run_quick_report(
                stores,
                pasted_text=pasted_text,
                uploaded_files=uploaded_files,
                template_type=template_type,
                output_format=output_format,
                project_id=target_project_id,
            )

        st.success(f"Report ready for project **{result.project_name}** ({result.project_id})")

        c1, c2, c3 = st.columns(3)
        c1.metric("Evidence ingested", len(result.evidence))
        c2.metric("Findings drafted", len(result.findings))
        c3.metric("Analysis errors", len(result.analysis_errors))

        if result.findings:
            st.markdown("**Drafted findings (severity pending your approval):**")
            for finding in result.findings:
                st.markdown(f"- {finding.title} — {severity_badge(finding.ai_suggested_severity)}", unsafe_allow_html=True)
            st.caption("Review and approve severities on the Findings page before treating this as final.")

        if result.analysis_errors:
            with st.expander(f"{len(result.analysis_errors)} evidence item(s) were not AI-analyzed"):
                for evidence, error in result.analysis_errors:
                    st.write(f"- {evidence.id} ({evidence.original_filename}): {error}")

        if result.findings_error:
            st.warning(f"Could not draft findings: {result.findings_error}")
        if result.narrative_error:
            st.warning(f"Could not write narrative sections: {result.narrative_error}")

        if result.output_path and result.output_path.exists():
            with open(result.output_path, "rb") as f:
                st.download_button(
                    f"Download {output_format.upper()} report", f.read(), file_name=result.output_path.name,
                )
        st.caption(
            f"Select **{result.project_name}** in the sidebar to review, edit, or regenerate "
            "this report using the Evidence, Findings, and Report Builder pages."
        )

    # ---------- Project overview (for whatever project is selected in the sidebar) ----------
    if sidebar_project is None:
        return

    st.divider()
    st.subheader(f"Overview: {sidebar_project.name}")

    evidence_list = stores.evidence_store.list_evidence(sidebar_project.id)
    findings_list = stores.findings_store.list_findings(sidebar_project.id)
    attack_nodes = stores.attack_path_store.list_nodes(sidebar_project.id)
    plan = stores.report_store.get_plan(sidebar_project.id)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Evidence items", len(evidence_list))
    c2.metric("Findings", len(findings_list))
    verified_nodes = sum(1 for n in attack_nodes if n.status == "verified")
    c3.metric("Attack path nodes", f"{verified_nodes}/{len(attack_nodes)} verified")
    c4.metric("Report plan", plan.template_type.upper() if plan else "Not started")

    unverified_evidence = [e for e in evidence_list if e.verification_status == "unverified"]
    if unverified_evidence:
        st.info(f"{len(unverified_evidence)} evidence item(s) awaiting human review on the Evidence page.")

    unapproved_findings = [f for f in findings_list if f.severity is None]
    if unapproved_findings:
        st.info(f"{len(unapproved_findings)} finding(s) have an AI-suggested severity awaiting your approval.")


if __name__ == "__main__":
    main()
