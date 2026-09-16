"""Projects page: list existing projects and their basic stats."""
from __future__ import annotations

import streamlit as st

from ui.common import get_stores, project_selector
from ui.style import apply_theme

st.set_page_config(page_title="Projects — NinjaReport AI", layout="wide")
apply_theme()
stores = get_stores()
project_selector(stores)

st.title("Projects")

projects = stores.evidence_store.list_projects()
if not projects:
    st.info("No projects yet. Use the sidebar to create one.")
else:
    for project in projects:
        with st.container(border=True):
            st.subheader(project.name)
            st.caption(f"ID: {project.id} · Created: {project.created_at}")
            evidence_count = len(stores.evidence_store.list_evidence(project.id))
            findings_count = len(stores.findings_store.list_findings(project.id))
            c1, c2 = st.columns(2)
            c1.metric("Evidence", evidence_count)
            c2.metric("Findings", findings_count)
