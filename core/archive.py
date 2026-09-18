"""Portable .nra project archives.

An .nra file is a plain zip containing:
  manifest.json          — format version, project id/name, per-file SHA-256
  db/project.sqlite3      — a fresh mini-database with ONLY this project's rows
  evidence/<project_id>/  — copies of every evidence file for this project
  derived/<project_id>/   — copies of preview/crop derived files
  exports/                — any past DOCX/PDF exports for this project

Import is collision-safe: if a project with the same ID already exists in
the destination, import is refused rather than silently overwritten or
auto-renamed. Every file's hash is verified against the manifest before
anything touches the live database — a corrupted or tampered archive is
rejected before any partial state can be created.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from core.db import get_connection, init_schema
from core.evidence_store import EvidenceStore
from core.hashing import sha256_file
from core.safe_archive import UnsafeArchiveError, safe_extract_zip

FORMAT_VERSION = "1"

# Tables copied verbatim, filtered by project_id, in FK-safe insert order.
_DIRECT_TABLES = [
    "evidence", "derived_artifacts", "crop_suggestions",
    "relationships", "findings", "attack_path_nodes", "report_plans",
]


class ArchiveError(ValueError):
    """Base class for archive export/import failures."""


class ArchiveImportError(ArchiveError):
    """Raised when an archive is corrupted, tampered with, unreadable, or
    would collide with an existing project."""


@dataclass
class ArchiveManifest:
    format_version: str
    exported_at: str
    project_id: str
    project_name: str
    file_hashes: dict[str, str]
    app: str = "NinjaReport AI"


def export_project(
    project_id: str,
    conn: sqlite3.Connection,
    settings,
    output_path: Path,
) -> Path:
    """Write a self-contained .nra archive for one project."""
    project_row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if project_row is None:
        raise ArchiveError(f"Unknown project: {project_id}")

    import tempfile
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        file_hashes: dict[str, str] = {}

        # ---- mini database ----
        mini_db_path = work / "db" / "project.sqlite3"
        mini_conn = get_connection(mini_db_path)
        init_schema(mini_conn)
        _copy_row(mini_conn, "projects", dict(project_row))
        for table in _DIRECT_TABLES:
            for row in conn.execute(f"SELECT * FROM {table} WHERE project_id = ?", (project_id,)).fetchall():
                _copy_row(mini_conn, table, dict(row))
        # report_sections/report_items nest under report_plans, not project_id directly.
        plan_ids = [
            r["id"] for r in conn.execute(
                "SELECT id FROM report_plans WHERE project_id = ?", (project_id,)
            ).fetchall()
        ]
        for plan_id in plan_ids:
            section_rows = conn.execute(
                "SELECT * FROM report_sections WHERE report_plan_id = ?", (plan_id,)
            ).fetchall()
            for srow in section_rows:
                _copy_row(mini_conn, "report_sections", dict(srow))
            section_ids = [r["id"] for r in section_rows]
            for section_id in section_ids:
                for irow in conn.execute(
                    "SELECT * FROM report_items WHERE report_section_id = ?", (section_id,)
                ).fetchall():
                    _copy_row(mini_conn, "report_items", dict(irow))
        mini_conn.close()
        file_hashes["db/project.sqlite3"] = sha256_file(mini_db_path)

        # ---- evidence files ----
        evidence_src_dir = settings.data_dir / "evidence" / project_id
        if evidence_src_dir.exists():
            for f in evidence_src_dir.rglob("*"):
                if f.is_file():
                    rel = f"evidence/{project_id}/{f.relative_to(evidence_src_dir)}"
                    dest = work / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dest)
                    file_hashes[rel] = sha256_file(dest)

        # ---- derived files ----
        derived_src_dir = settings.data_dir / "derived" / project_id
        if derived_src_dir.exists():
            for f in derived_src_dir.rglob("*"):
                if f.is_file():
                    rel = f"derived/{project_id}/{f.relative_to(derived_src_dir)}"
                    dest = work / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dest)
                    file_hashes[rel] = sha256_file(dest)

        # ---- past exports ----
        export_src_dir = settings.export_dir / project_id
        if export_src_dir.exists():
            for f in export_src_dir.rglob("*"):
                if f.is_file():
                    rel = f"exports/{project_id}/{f.relative_to(export_src_dir)}"
                    dest = work / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dest)
                    file_hashes[rel] = sha256_file(dest)

        # ---- manifest ----
        manifest = {
            "format_version": FORMAT_VERSION,
            "exported_at": datetime.now(UTC).isoformat(),
            "project_id": project_id,
            "project_name": project_row["name"],
            "app": "NinjaReport AI",
            "file_hashes": file_hashes,
        }
        (work / "manifest.json").write_text(json.dumps(manifest, indent=2))

        # ---- zip it up ----
        output_path.parent.mkdir(parents=True, exist_ok=True)
        import zipfile
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in work.rglob("*"):
                if f.is_file():
                    zf.write(f, arcname=str(f.relative_to(work)))

    return output_path


def read_manifest(archive_path: Path) -> ArchiveManifest:
    """Peek at an archive's manifest without extracting anything else."""
    import zipfile
    try:
        with zipfile.ZipFile(archive_path) as zf:
            data = json.loads(zf.read("manifest.json"))
    except (zipfile.BadZipFile, KeyError) as exc:
        raise ArchiveImportError(f"Not a valid NinjaReport AI archive: {exc}") from exc
    return ArchiveManifest(**data)


def import_project(archive_path: Path, conn: sqlite3.Connection, settings) -> str:
    """Import a .nra archive as a new project. Returns the imported
    project_id. Refuses (ArchiveImportError) on: corrupted archive, hash
    mismatch against the manifest, unsupported format version, or a
    project_id collision with an existing project."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        extract_dir = Path(td) / "extracted"
        try:
            safe_extract_zip(archive_path, extract_dir)
        except UnsafeArchiveError as exc:
            raise ArchiveImportError(str(exc)) from exc

        manifest_path = extract_dir / "manifest.json"
        if not manifest_path.exists():
            raise ArchiveImportError("Archive is missing manifest.json.")
        manifest = ArchiveManifest(**json.loads(manifest_path.read_text()))

        if manifest.format_version != FORMAT_VERSION:
            raise ArchiveImportError(
                f"Unsupported archive format version: {manifest.format_version} "
                f"(expected {FORMAT_VERSION})."
            )

        # Verify every recorded file's hash BEFORE touching the live DB.
        for rel_path, expected_hash in manifest.file_hashes.items():
            full_path = extract_dir / rel_path
            if not full_path.exists():
                raise ArchiveImportError(f"Archive is missing a file listed in its manifest: {rel_path}")
            actual_hash = sha256_file(full_path)
            if actual_hash != expected_hash:
                raise ArchiveImportError(
                    f"Hash mismatch for {rel_path} — archive is corrupted or was tampered with."
                )

        evidence_store = EvidenceStore(conn, evidence_root=settings.data_dir / "evidence")
        if evidence_store.get_project(manifest.project_id) is not None:
            raise ArchiveImportError(
                f"A project with ID {manifest.project_id} already exists. "
                "Delete or rename it before importing, to avoid overwriting data."
            )

        project_id = manifest.project_id

        # ---- copy files into live storage ----
        for area in ("evidence", "derived", "exports"):
            src_dir = extract_dir / area / project_id
            if not src_dir.exists():
                continue
            dest_root = (
                settings.data_dir / "evidence" if area == "evidence"
                else settings.data_dir / "derived" if area == "derived"
                else settings.export_dir
            ) / project_id
            for f in src_dir.rglob("*"):
                if f.is_file():
                    dest = dest_root / f.relative_to(src_dir)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dest)

        # ---- copy rows from the mini db into the live db, atomically ----
        # evidence/findings/derived_artifacts/crop_suggestions/relationships/
        # attack_path_nodes use business-string IDs (EVD-.../FIND-...) that
        # stay valid regardless of which numeric surrogate PK wraps them, so
        # those surrogate PKs can be safely dropped and reassigned. But
        # report_plans.id / report_sections.id are numeric AUTOINCREMENT PKs
        # that report_sections.report_plan_id / report_items.report_section_id
        # reference directly — copying those verbatim into a live DB that
        # already has other projects' rows (and thus its own overlapping
        # PK sequence) would collide or silently misattach items to the
        # wrong section. Those two must be remapped old-id -> new-id.
        mini_conn = get_connection(extract_dir / "db" / "project.sqlite3")
        conn.execute("BEGIN IMMEDIATE")
        try:
            project_row = mini_conn.execute(
                "SELECT * FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            _copy_row(conn, "projects", dict(project_row))

            # evidence: drop the surrogate rowid_pk, keep the business id ("EVD-...").
            for row in mini_conn.execute("SELECT * FROM evidence").fetchall():
                _insert_dropping_column(conn, "evidence", dict(row), "rowid_pk")

            # findings: same — drop rowid_pk, keep the business id ("FIND-...").
            for row in mini_conn.execute("SELECT * FROM findings").fetchall():
                _insert_dropping_column(conn, "findings", dict(row), "rowid_pk")

            # Tables whose numeric `id` is never referenced elsewhere:
            # safe to drop and let sqlite assign a fresh one.
            for table in ("derived_artifacts", "crop_suggestions", "relationships", "attack_path_nodes"):
                for row in mini_conn.execute(f"SELECT * FROM {table}").fetchall():
                    _insert_dropping_column(conn, table, dict(row), "id")

            # report_plans -> report_sections -> report_items: numeric ids
            # ARE referenced across these three tables, so remap them.
            plan_id_map: dict[int, int] = {}
            for row in mini_conn.execute("SELECT * FROM report_plans").fetchall():
                old_id = row["id"]
                new_id = _insert_dropping_column(conn, "report_plans", dict(row), "id")
                plan_id_map[old_id] = new_id

            section_id_map: dict[int, int] = {}
            for row in mini_conn.execute("SELECT * FROM report_sections").fetchall():
                data = dict(row)
                old_id = data["id"]
                data["report_plan_id"] = plan_id_map[data["report_plan_id"]]
                new_id = _insert_dropping_column(conn, "report_sections", data, "id")
                section_id_map[old_id] = new_id

            for row in mini_conn.execute("SELECT * FROM report_items").fetchall():
                data = dict(row)
                data["report_section_id"] = section_id_map[data["report_section_id"]]
                _insert_dropping_column(conn, "report_items", data, "id")

            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            mini_conn.close()

    return project_id


def _copy_row(conn: sqlite3.Connection, table: str, row: dict) -> None:
    columns = list(row.keys())
    placeholders = ", ".join("?" for _ in columns)
    column_list = ", ".join(columns)
    conn.execute(
        f"INSERT INTO {table} ({column_list}) VALUES ({placeholders})",
        [row[c] for c in columns],
    )


def _insert_dropping_column(conn: sqlite3.Connection, table: str, row: dict, drop_column: str) -> int:
    """Insert a row omitting `drop_column` (an autoincrement surrogate key
    that must not be copied verbatim into a shared live database) and
    return the newly assigned rowid for that column."""
    row = dict(row)
    row.pop(drop_column, None)
    columns = list(row.keys())
    placeholders = ", ".join("?" for _ in columns)
    column_list = ", ".join(columns)
    cur = conn.execute(
        f"INSERT INTO {table} ({column_list}) VALUES ({placeholders})",
        [row[c] for c in columns],
    )
    return cur.lastrowid
