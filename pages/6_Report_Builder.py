"""Report Builder page."""
from __future__ import annotations

import streamlit as st

from ui.common import get_stores, project_selector
from ui.style import apply_theme

st.set_page_config(page_title="Report Builder — NinjaReport AI", layout="wide")
apply_theme()
stores = get_stores()
project = project_selector(stores)

st.title("Report Builder")

if project is None:
    st.info("Create a project first.")
    st.stop()

plan = stores.report_store.get_plan(project.id)

if plan is None:
    st.subheader("Start a report")
    template = st.radio("Template", ["ctf", "vapt"], format_func=str.upper)
    if st.button("Create report plan"):
        stores.report_store.create_plan(project.id, template)
        st.rerun()
    st.stop()

st.caption(f"Template: {plan.template_type.upper()}")
if st.button("Reset plan (delete and start over)"):
    stores.report_store.delete_plan(project.id)
    st.rerun()

evidence_options = {e.id: e.original_filename for e in stores.evidence_store.list_evidence(project.id)}
finding_options = {f.id: f.title for f in stores.findings_store.list_findings(project.id)}

section_ids = [s.id for s in plan.sections]
for i, section in enumerate(plan.sections):
    with st.container(border=True):
        col_title, col_toggle, col_up, col_down = st.columns([4, 2, 1, 1])
        col_title.markdown(f"### {section.title}")
        include = col_toggle.checkbox("Included", value=section.included, key=f"sec_inc_{section.id}")
        if include != section.included:
            stores.report_store.set_section_included(section.id, include)
            st.rerun()

        if col_up.button("↑", key=f"up_{section.id}") and i > 0:
            reordered = section_ids.copy()
            reordered[i - 1], reordered[i] = reordered[i], reordered[i - 1]
            stores.report_store.reorder_sections(project.id, reordered)
            st.rerun()
        if col_down.button("↓", key=f"down_{section.id}") and i < len(section_ids) - 1:
            reordered = section_ids.copy()
            reordered[i + 1], reordered[i] = reordered[i], reordered[i + 1]
            stores.report_store.reorder_sections(project.id, reordered)
            st.rerun()

        for item in section.items:
            label = {
                "evidence": f"📷 {evidence_options.get(item.evidence_id, item.evidence_id)}",
                "finding": f"⚠️ {finding_options.get(item.finding_id, item.finding_id)}",
                "text": f"📝 {(item.text_content or '')[:60]}",
            }[item.item_type]
            inc = st.checkbox(label, value=item.included, key=f"item_inc_{item.id}")
            if inc != item.included:
                stores.report_store.set_item_included(item.id, inc)
                st.rerun()

        with st.expander(f"Add to {section.title}"):
            item_kind = st.selectbox(
                "Type", ["evidence", "finding", "text"], key=f"kind_{section.id}",
            )
            caption = st.text_input("Caption", key=f"caption_{section.id}")
            if item_kind == "evidence" and evidence_options:
                chosen = st.selectbox("Evidence", list(evidence_options.keys()), key=f"pick_ev_{section.id}")
                if st.button("Add evidence item", key=f"add_ev_{section.id}"):
                    stores.report_store.add_evidence_item(
                        project.id, section.id, chosen, caption=caption, evidence_store=stores.evidence_store,
                    )
                    st.rerun()
            elif item_kind == "finding" and finding_options:
                chosen = st.selectbox("Finding", list(finding_options.keys()), key=f"pick_find_{section.id}")
                if st.button("Add finding item", key=f"add_find_{section.id}"):
                    stores.report_store.add_finding_item(
                        project.id, section.id, chosen, caption=caption, findings_store=stores.findings_store,
                    )
                    st.rerun()
            elif item_kind == "text":
                text_content = st.text_area("Text", key=f"text_{section.id}")
                if st.button("Add text item", key=f"add_text_{section.id}") and text_content.strip():
                    stores.report_store.add_text_item(section.id, text_content, caption=caption)
                    st.rerun()
