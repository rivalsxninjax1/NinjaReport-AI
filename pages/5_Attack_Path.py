"""Attack Path page. Node status is never a manual toggle — it's computed
live by AttackPathStore from whatever evidence is attached, exactly as
enforced in Phase 6.
"""
from __future__ import annotations

import streamlit as st

from core.models import ATTACK_STAGE_ORDER
from ui.common import get_stores, project_selector
from ui.style import apply_theme, status_badge

st.set_page_config(page_title="Attack Path — NinjaReport AI", layout="wide")
apply_theme()
stores = get_stores()
project = project_selector(stores)

st.title("Attack Path")

if project is None:
    st.info("Create a project first.")
    st.stop()

evidence_ids = [e.id for e in stores.evidence_store.list_evidence(project.id)]

with st.expander("Add stage node"):
    stage = st.selectbox("Stage", ATTACK_STAGE_ORDER)
    node_title = st.text_input("Title")
    node_description = st.text_area("Description")
    node_evidence = st.multiselect("Supporting evidence (leave empty to stay unverified)", evidence_ids)
    if st.button("Add node") and node_title.strip():
        stores.attack_path_store.add_node(
            project.id, stage=stage, title=node_title.strip(), description=node_description,
            evidence_ids=node_evidence, evidence_store=stores.evidence_store,
        )
        st.rerun()

nodes = stores.attack_path_store.list_nodes(project.id)
if not nodes:
    st.info("No attack path nodes yet.")
    st.stop()

for node in nodes:
    with st.container(border=True):
        st.markdown(f"**[{node.stage}]** {node.title} — {status_badge(node.status)}", unsafe_allow_html=True)
        if node.description:
            st.write(node.description)

        current = node.evidence_ids
        updated = st.multiselect(
            "Supporting evidence", evidence_ids, default=current, key=f"node_evidence_{node.id}",
        )
        if updated != current and st.button("Update evidence", key=f"update_{node.id}"):
            stores.attack_path_store.update_evidence(node.id, updated, evidence_store=stores.evidence_store)
            st.rerun()
