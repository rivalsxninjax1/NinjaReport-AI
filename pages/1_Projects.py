"""Projects page: list projects, see their stats, and rename them.

Exporting, importing, backing up, and deleting projects lives on the
Archive page.
"""
from __future__ import annotations

import streamlit as st

from ui.common import flash, get_stores, project_selector, show_flash
from ui.style import apply_theme

st.set_page_config(page_title="Projects — NinjaReport AI", layout="wide")
apply_theme()
stores = get_stores()
project_selector(stores)

st.title("Projects")
show_flash()

projects = stores.evidence_store.list_projects()
if not projects:
    st.info("No projects yet. Create one in the sidebar, or go to the home page and generate a Quick Report.")
    st.stop()

for project in projects:
    with st.container(border=True):
        st.subheader(project.name)
        st.caption(f"ID: {project.id} · Created: {project.created_at}")
        c1, c2 = st.columns(2)
        c1.metric("Evidence", len(stores.evidence_store.list_evidence(project.id)))
        c2.metric("Findings", len(stores.findings_store.list_findings(project.id)))

        with st.expander("Rename"):
            new_name = st.text_input("New name", value=project.name, key=f"rename_{project.id}")
            if st.button("Save name", key=f"save_name_{project.id}"):
                try:
                    stores.evidence_store.rename_project(project.id, new_name)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    flash("success", f"Renamed to “{new_name.strip()}”.")
                    st.rerun()

st.caption("To export, back up, restore, or delete a project, use the Archive page.")
