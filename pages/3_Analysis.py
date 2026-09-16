"""Analysis page: human-initiated AI evidence analysis (never automatic)."""
from __future__ import annotations

import streamlit as st

from ai.evidence_analyzer import analyze_evidence
from ai.provider import AIProviderError, OfflineModeError
from ui.common import get_stores, project_selector
from ui.style import apply_theme

st.set_page_config(page_title="Analysis — NinjaReport AI", layout="wide")
apply_theme()
stores = get_stores()
project = project_selector(stores)

st.title("Evidence Analysis")

if project is None:
    st.info("Create a project first.")
    st.stop()

if not stores.settings.ollama_text_model:
    st.warning("No OLLAMA_TEXT_MODEL configured in .env — analysis is unavailable until one is set.")
    st.stop()

evidence_list = stores.evidence_store.list_evidence(project.id)
if not evidence_list:
    st.info("No evidence yet — add some on the Evidence page.")
    st.stop()

labels = {f"{e.id} — {e.original_filename}": e for e in evidence_list}
choice = st.selectbox("Evidence", list(labels.keys()))
evidence = labels[choice]

if st.button("Run AI analysis", key="run_analysis"):
    with st.spinner("Analyzing..."):
        try:
            result = analyze_evidence(
                evidence, stores.derived_store, stores.ollama, stores.ai_cache,
                model=stores.settings.ollama_text_model,
            )
            st.session_state["last_analysis"] = result
        except OfflineModeError as exc:
            st.error(f"Ollama is offline — analysis skipped: {exc}")
        except AIProviderError as exc:
            st.error(f"Analysis failed: {exc}")

result = st.session_state.get("last_analysis")
if result and result.evidence_id == evidence.id:
    st.subheader("Classification")
    st.write(result.classification)

    st.subheader("Summary")
    st.write(result.summary)

    if result.technical_facts:
        st.subheader("Technical facts (source-attributed, unverified)")
        for fact in result.technical_facts:
            st.markdown(f"- **{fact.fact}** (confidence {fact.confidence:.0%}) — *{fact.source}*")

    cols = st.columns(3)
    cols[0].write("**Commands**\n" + "\n".join(result.commands) if result.commands else "")
    cols[1].write("**IPs**\n" + "\n".join(result.ips) if result.ips else "")
    cols[2].write("**Ports**\n" + ", ".join(str(p) for p in result.ports) if result.ports else "")

    if result.uncertainties:
        st.warning("Uncertainties flagged by the AI (needs human judgment):")
        for u in result.uncertainties:
            st.markdown(f"- {u}")

    st.caption(
        f"Verification status: {result.verification_status.upper()} — "
        "AI analysis can never mark itself verified; review it, then update the evidence's "
        "verification status on the Evidence page yourself."
    )
