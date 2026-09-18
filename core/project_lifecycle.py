"""Project deletion.

delete_project_cascade() does the actual irreversible work — it has no
safety gate of its own, by design, so it stays simple and testable.
safe_delete_project() is the gate: it structurally requires a verified,
matching export to already exist before calling the cascade, unless the
caller explicitly opts out.
"""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from core.archive import ArchiveError, read_manifest


def delete_project_cascade(
    conn: sqlite3.Connection, evidence_root: Path, derived_root: Path, export_dir: Path, project_id: str,
) -> None:
    """Irreversibly delete a project: all DB rows, evidence files, derived
    files, and past exports. No export-first check here — that's
    safe_delete_project()'s job."""
    shutil.rmtree(evidence_root / project_id, ignore_errors=True)
    shutil.rmtree(derived_root / project_id, ignore_errors=True)
    shutil.rmtree(export_dir / project_id, ignore_errors=True)

    conn.execute("BEGIN IMMEDIATE")
    try:
        section_ids = [
            r["id"] for r in conn.execute(
                "SELECT rs.id FROM report_sections rs "
                "JOIN report_plans rp ON rs.report_plan_id = rp.id WHERE rp.project_id = ?",
                (project_id,),
            ).fetchall()
        ]
        for section_id in section_ids:
            conn.execute("DELETE FROM report_items WHERE report_section_id = ?", (section_id,))
        conn.execute(
            "DELETE FROM report_sections WHERE report_plan_id IN "
            "(SELECT id FROM report_plans WHERE project_id = ?)",
            (project_id,),
        )
        conn.execute("DELETE FROM report_plans WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM attack_path_nodes WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM findings WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM relationships WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM crop_suggestions WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM derived_artifacts WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM evidence WHERE project_id = ?", (project_id,))
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def safe_delete_project(
    conn: sqlite3.Connection,
    settings,
    project_id: str,
    export_path: Path | None = None,
    allow_skip_export: bool = False,
) -> None:
    """Delete a project, but only if a matching export already exists —
    unless allow_skip_export=True is passed explicitly (a deliberate,
    named override, never the default)."""
    if not allow_skip_export:
        if export_path is None or not Path(export_path).exists():
            raise ArchiveError(
                "Refusing to delete: no export was provided. Export the project "
                "first (Exports/Archive page), or pass allow_skip_export=True explicitly."
            )
        try:
            manifest = read_manifest(export_path)
        except ArchiveError as exc:
            raise ArchiveError(f"Refusing to delete: provided export is invalid: {exc}") from exc
        if manifest.project_id != project_id:
            raise ArchiveError(
                f"Refusing to delete: the provided export is for project "
                f"{manifest.project_id}, not {project_id}."
            )

    delete_project_cascade(
        conn, settings.data_dir / "evidence", settings.data_dir / "derived", settings.export_dir, project_id,
    )
