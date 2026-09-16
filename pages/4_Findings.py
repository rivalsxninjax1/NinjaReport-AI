"""Findings page. The severity approval gate from Phase 6 is a real UI
action here: 'Suggest severity (AI)' only ever writes the advisory
ai_suggested_severity field; a separate 'Approve severity' button — which
defaults to but does not require the AI's suggestion — is the only control
that sets the finding's effective severity.
"""
from __future__ import annotations

import streamlit as st

from ai.provider import AIProviderError, OfflineModeError
from core.findings_store import VALID_SEVERITIES
from ui.common import get_stores, project_selector
from ui.style import apply_theme, severity_badge

st.set_page_config(page_title="Findings — NinjaReport AI", layout="wide")
apply_theme()
stores = get_stores()
project = project_selector(stores)

st.title("Findings")

if project is None:
    st.info("Create a project first.")
    st.stop()

with st.expander("New finding"):
    title = st.text_input("Title")
    affected_asset = st.text_input("Affected asset")
    description = st.text_area("Description")
    evidence_ids = st.multiselect(
        "Supporting evidence",
        [e.id for e in stores.evidence_store.list_evidence(project.id)],
    )
    if st.button("Create finding") and title.strip():
        stores.findings_store.create_finding(
            project.id, title=title.strip(), affected_asset=affected_asset,
            description=description, evidence_ids=evidence_ids,
            evidence_store=stores.evidence_store,
        )
        st.rerun()

findings = stores.findings_store.list_findings(project.id)
if not findings:
    st.info("No findings yet.")
    st.stop()

for finding in findings:
    with st.container(border=True):
        st.markdown(f"### {finding.id} — {finding.title}")
        st.markdown(f"Severity: {severity_badge(finding.severity)}", unsafe_allow_html=True)
        st.caption(f"Status: {finding.status} · Affected: {finding.affected_asset or 'unspecified'}")
        if finding.description:
            st.write(finding.description)

        col1, col2 = st.columns(2)

        with col1:
            st.markdown("**AI severity suggestion**")
            if finding.ai_suggested_severity:
                st.caption(f"Suggested: {finding.ai_suggested_severity} — {finding.ai_severity_rationale}")
            if st.button("Suggest severity (AI)", key=f"suggest_{finding.id}"):
                if not stores.settings.ollama_text_model:
                    st.error("No OLLAMA_TEXT_MODEL configured.")
                else:
                    prompt = (
                        "Given this vulnerability finding, suggest a severity "
                        "(informational, low, medium, high, or critical) and a one-sentence rationale. "
                        f"Title: {finding.title}\nDescription: {finding.description}\n"
                        'Return ONLY JSON: {"severity": "...", "rationale": "..."}'
                    )
                    try:
                        raw = stores.ollama.generate_json(prompt, model=stores.settings.ollama_text_model)
                        severity = raw.get("severity", "")
                        rationale = raw.get("rationale", "")
                        if severity in VALID_SEVERITIES:
                            stores.findings_store.suggest_severity(project.id, finding.id, severity, rationale)
                            st.rerun()
                        else:
                            st.error(f"AI returned an invalid severity: {severity!r}")
                    except OfflineModeError as exc:
                        st.error(f"Ollama offline: {exc}")
                    except AIProviderError as exc:
                        st.error(f"Suggestion failed: {exc}")

        with col2:
            st.markdown("**Human approval (required to take effect)**")
            severity_options = sorted(VALID_SEVERITIES)
            default_index = (
                severity_options.index(finding.ai_suggested_severity)
                if finding.ai_suggested_severity in severity_options else 0
            )
            chosen = st.selectbox(
                "Approve as", severity_options, index=default_index, key=f"approve_select_{finding.id}",
            )
            if st.button("Approve severity", key=f"approve_{finding.id}"):
                stores.findings_store.approve_severity(project.id, finding.id, chosen)
                st.rerun()

        cvss = st.text_input(
            "CVSS vector (free text, never auto-generated)",
            finding.cvss_vector or "", key=f"cvss_{finding.id}",
        )
        if st.button("Save CVSS vector", key=f"save_cvss_{finding.id}") and cvss != (finding.cvss_vector or ""):
            stores.findings_store.set_cvss_vector(project.id, finding.id, cvss)
            st.rerun()
