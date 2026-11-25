"""Analysis page: human-initiated AI evidence analysis (never automatic)."""
from __future__ import annotations

import streamlit as st

from ai.evidence_analyzer import PROMPT_VERSION, analyze_evidence
from ai.provider import AIProviderError, OfflineModeError
from ai.schemas import EvidenceAnalysis
from ui.common import get_stores, project_selector, show_flash
from ui.style import apply_theme

st.set_page_config(page_title="Analysis — NinjaReport AI", layout="wide")
apply_theme()
stores = get_stores()
project = project_selector(stores)

st.title("Evidence Analysis")
show_flash()

if project is None:
    st.info("Create a project first.")
    st.stop()

model = stores.settings.ollama_text_model
if not model:
    st.warning("No OLLAMA_TEXT_MODEL configured in .env — analysis is unavailable until one is set.")
    st.stop()

evidence_list = stores.evidence_store.list_evidence(project.id)
if not evidence_list:
    st.info("No evidence yet — add some on the Evidence page.")
    st.stop()

# ---------- Analyze everything ----------
with st.container(border=True):
    st.markdown("**Analyze all evidence at once**")
    st.caption(
        "Items already analyzed come straight from the cache, so re-running this is cheap. "
        "Items are processed one at a time to keep memory use low."
    )
    if st.button("Analyze all evidence", key="analyze_all"):
        progress = st.progress(0.0, text="Starting...")
        succeeded, failures = 0, []
        for i, item in enumerate(evidence_list):
            progress.progress(i / len(evidence_list), text=f"Analyzing {item.id} ({i + 1}/{len(evidence_list)})")
            try:
                analyze_evidence(item, stores.derived_store, stores.ollama, stores.ai_cache, model=model)
                succeeded += 1
            except OfflineModeError as exc:
                failures.append((item, str(exc)))
                break  # Ollama is down; no point trying the rest
            except AIProviderError as exc:
                failures.append((item, str(exc)))
        progress.progress(1.0, text="Done.")
        st.success(f"Analyzed {succeeded} of {len(evidence_list)} item(s).")
        for item, error in failures:
            st.error(f"{item.id} ({item.original_filename}): {error}")

# ---------- One item at a time ----------
st.subheader("Review one item")
labels = {f"{e.id} — {e.original_filename}": e for e in evidence_list}
evidence = labels[st.selectbox("Evidence", list(labels.keys()))]

result = None
cached = stores.ai_cache.get(evidence.sha256, model, PROMPT_VERSION)
if cached:
    result = EvidenceAnalysis(**cached)

if st.button("Run AI analysis", key="run_analysis"):
    with st.spinner("Analyzing..."):
        try:
            result = analyze_evidence(evidence, stores.derived_store, stores.ollama, stores.ai_cache, model=model)
        except OfflineModeError as exc:
            st.error(f"Ollama is offline — analysis skipped: {exc}")
        except AIProviderError as exc:
            st.error(f"Analysis failed: {exc}")

if result is None:
    st.caption("Not analyzed yet. Click “Run AI analysis”, or use “Analyze all evidence” above.")
else:
    st.subheader("Classification")
    st.write(result.classification)

    st.subheader("Summary")
    st.write(result.summary)

    if result.technical_facts:
        st.subheader("Technical facts (source-attributed, unverified)")
        for fact in result.technical_facts:
            st.markdown(f"- **{fact.fact}** (confidence {fact.confidence:.0%}) — *{fact.source}*")

    cols = st.columns(3)
    if result.commands:
        cols[0].markdown("**Commands**\n" + "\n".join(f"- `{c}`" for c in result.commands))
    if result.ips:
        cols[1].markdown("**IPs**\n" + "\n".join(f"- {ip}" for ip in result.ips))
    if result.ports:
        cols[2].markdown("**Ports**\n" + "\n".join(f"- {p}" for p in result.ports))

    if result.uncertainties:
        st.warning("Uncertainties flagged by the AI (needs human judgment):")
        for u in result.uncertainties:
            st.markdown(f"- {u}")

    st.caption(
        f"Verification status: {result.verification_status.upper()} — "
        "AI analysis can never mark itself verified; review it, then update the evidence's "
        "verification status on the Evidence page yourself."
    )
