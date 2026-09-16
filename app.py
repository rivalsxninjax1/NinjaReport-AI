"""NinjaReport AI — Dashboard (Streamlit entry point)."""
from __future__ import annotations

import sys

from config import get_settings
from core.logging_setup import configure_logging


def bootstrap():
    """Load settings, ensure directories exist, configure logging. Kept
    dependency-free (no streamlit import) so it's unit testable."""
    settings = get_settings()
    settings.ensure_directories()
    logger = configure_logging(settings.data_dir)
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
    from ui.common import get_stores, project_selector
    from ui.style import apply_theme

    st.set_page_config(page_title="NinjaReport AI", layout="wide")
    apply_theme()

    stores = get_stores()
    st.title("NinjaReport AI")
    st.caption("Local-first CTF/VAPT evidence-to-report generator")

    project = project_selector(stores)

    diagnostics = run_diagnostics(
        stores.ollama, stores.settings.ollama_text_model, stores.settings.ollama_vision_model,
    )
    col1, col2, col3 = st.columns(3)
    col1.metric("Ollama", "Reachable" if diagnostics.reachable else "Offline")
    col2.metric("Text model", "Ready" if diagnostics.text_model_ready else "Not ready")
    col3.metric("Vision model", "Ready" if diagnostics.vision_model_ready else "Not ready")

    if not diagnostics.reachable:
        st.warning(
            "Ollama is offline — AI analysis features will be skipped until it's reachable. "
            "Evidence storage, OCR, findings, and reports all still work fully offline."
        )

    if project is None:
        st.info("Create a project in the sidebar to get started.")
        return

    st.divider()
    st.subheader(f"Overview: {project.name}")

    evidence_list = stores.evidence_store.list_evidence(project.id)
    findings_list = stores.findings_store.list_findings(project.id)
    attack_nodes = stores.attack_path_store.list_nodes(project.id)
    plan = stores.report_store.get_plan(project.id)

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
