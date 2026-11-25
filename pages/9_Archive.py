"""Archive page: portable project archives, database backups, and safe project deletion."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import streamlit as st

from core.archive import ArchiveError, export_project, import_project
from core.db_backup import backup_database, list_backups, restore_database
from core.project_lifecycle import safe_delete_project
from ui.common import (
    destructive_button,
    flash,
    get_stores,
    project_selector,
    save_uploaded_bytes,
    show_flash,
)
from ui.style import apply_theme

st.set_page_config(page_title="Archive — NinjaReport AI", layout="wide")
apply_theme()
stores = get_stores()
project_selector(stores)

st.title("Archive & Backup")
show_flash()

settings = stores.settings
# Archives are kept OUTSIDE exports/ so one archive never gets bundled into the next.
archives_dir = settings.data_dir / "archives"
backups_dir = settings.data_dir / "backups"
projects = stores.evidence_store.list_projects()


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d_%H%M%S")


tab_export, tab_backup, tab_delete = st.tabs(["Export / Import", "Database backups", "Delete a project"])

with tab_export:
    st.subheader("Export a project")
    st.caption(
        "A .nra file is a portable, hash-checked copy of one project: its evidence, findings, "
        "report plans, and past reports."
    )
    if not projects:
        st.info("No projects to export yet.")
    else:
        labels = {f"{p.name} ({p.id})": p for p in projects}
        chosen = labels[st.selectbox("Project", list(labels), key="export_project")]
        if st.button("Create .nra archive", key="export_button"):
            out = archives_dir / f"{chosen.id}_{_timestamp()}.nra"
            try:
                export_project(chosen.id, stores.conn, settings, out)
            except ArchiveError as exc:
                st.error(f"Export failed: {exc}")
            else:
                st.session_state["last_archive"] = str(out)
                st.success(f"Created {out.name}")

        last = st.session_state.get("last_archive")
        if last and Path(last).exists():
            with open(last, "rb") as fh:
                st.download_button("Download archive", fh.read(), file_name=Path(last).name, mime="application/zip")
            st.caption(f"Also saved on this computer at {last}")

    st.divider()
    st.subheader("Import a project")
    st.caption("Importing never overwrites anything: if a project with the same ID already exists, the import is refused.")
    uploaded = st.file_uploader("Choose a .nra archive", type=["nra"], key="import_uploader")
    if uploaded and st.button("Import project", key="import_button"):
        saved = save_uploaded_bytes(uploaded.getvalue(), uploaded.name, settings.data_dir / "uploads_scratch")
        try:
            imported_id = import_project(saved, stores.conn, settings)
        except ArchiveError as exc:
            st.error(f"Import failed: {exc}")
        else:
            flash("success", f"Imported project {imported_id}. Pick it in the sidebar.")
            st.rerun()
        finally:
            saved.unlink(missing_ok=True)

with tab_backup:
    st.subheader("Database backups")
    st.caption(
        "A backup is a snapshot of the database only (projects, findings, report plans). Evidence files "
        "stay in your data folder — use Export for a fully self-contained copy of a project."
    )
    if st.button("Create backup now", key="backup_button"):
        path = backup_database(settings.db_path(), backups_dir)
        flash("success", f"Backup saved: {path.name}")
        st.rerun()

    backups = list_backups(backups_dir)
    if not backups:
        st.info("No backups yet.")
    for backup in backups:
        with st.container(border=True):
            st.write(f"**{backup.name}** — {backup.stat().st_size / 1024:.0f} KB")
            if destructive_button(
                "Restore this backup",
                key=f"restore_{backup.name}",
                confirm_text="Yes, replace current data with this backup",
                warning="Your current database is kept as a safety copy, but you must restart the app afterwards.",
            ):
                preserved = restore_database(backup, settings.db_path())
                note = f" Your previous database was kept as {preserved.name}." if preserved else ""
                flash("warning", f"Restored {backup.name}.{note} Please restart the app now so it loads the restored data.")
                st.rerun()

with tab_delete:
    st.subheader("Delete a project")
    st.warning(
        "Deleting removes the project's evidence, findings, and reports from this computer. "
        "A portable .nra archive is saved automatically first, so you can import it back later."
    )
    if not projects:
        st.info("No projects to delete.")
    else:
        labels = {f"{p.name} ({p.id})": p for p in projects}
        chosen = labels[st.selectbox("Project", list(labels), key="delete_project_choice")]
        if destructive_button(
            "Export, then delete this project",
            key=f"delete_project_{chosen.id}",
            confirm_text="Yes, export and permanently delete",
            warning=f"“{chosen.name}” will be removed from this computer (a safety archive is saved first).",
        ):
            archive = archives_dir / f"{chosen.id}_before_delete_{_timestamp()}.nra"
            try:
                export_project(chosen.id, stores.conn, settings, archive)
                safe_delete_project(stores.conn, settings, chosen.id, export_path=archive)
            except ArchiveError as exc:
                st.error(f"Nothing was deleted: {exc}")
            else:
                flash("success", f"Deleted “{chosen.name}”. Safety archive: {archive}")
                st.rerun()
