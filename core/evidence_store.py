"""Project and evidence persistence.

Evidence originals are copied (never moved/modified) into
<data_dir>/evidence/<project_id>/<EVD-xxx>_<safe_filename> and made
read-only. Duplicate detection is enforced both here and at the DB level
(UNIQUE(project_id, sha256)) as defense in depth.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from core.hashing import sha256_file
from core.models import Evidence, Project
from core.safe_files import InvalidEvidenceError, resolve_within, sanitize_filename, validate_upload


class DuplicateEvidenceError(ValueError):
    """Raised when a file with the same SHA-256 already exists in the project."""

    def __init__(self, existing_evidence_id: str):
        self.existing_evidence_id = existing_evidence_id
        super().__init__(f"Duplicate of existing evidence {existing_evidence_id}")


class EvidenceStore:
    def __init__(self, conn: sqlite3.Connection, evidence_root: Path):
        self.conn = conn
        self.evidence_root = evidence_root
        self.evidence_root.mkdir(parents=True, exist_ok=True)

    # ---------- Projects ----------

    def create_project(self, name: str) -> Project:
        project_id = f"PRJ-{uuid.uuid4().hex[:8]}"
        created_at = datetime.now(UTC).isoformat()
        self.conn.execute(
            "INSERT INTO projects (id, name, created_at, next_evidence_seq) VALUES (?, ?, ?, 1)",
            (project_id, name, created_at),
        )
        return Project(id=project_id, name=name, created_at=created_at, next_evidence_seq=1)

    def get_project(self, project_id: str) -> Project | None:
        row = self.conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if row is None:
            return None
        return Project(**dict(row))

    def list_projects(self) -> list[Project]:
        rows = self.conn.execute("SELECT * FROM projects ORDER BY created_at").fetchall()
        return [Project(**dict(r)) for r in rows]

    # ---------- Evidence ----------

    def _next_evidence_id(self, project_id: str) -> str:
        """Atomically reserve the next sequential evidence ID for a project."""
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute(
                "SELECT next_evidence_seq FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"Unknown project: {project_id}")
            seq = row["next_evidence_seq"]
            self.conn.execute(
                "UPDATE projects SET next_evidence_seq = ? WHERE id = ?",
                (seq + 1, project_id),
            )
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        return f"EVD-{seq:03d}"

    def add_evidence(
        self,
        project_id: str,
        source_path: Path,
        original_filename: str,
        evidence_type: str,
        max_upload_mb: int,
        source_type: str = "other",
        description: str = "",
        tags: list[str] | None = None,
        host: str | None = None,
        port: int | None = None,
        allow_duplicate: bool = False,
    ) -> Evidence:
        """Validate, hash, copy immutably, and record a new evidence file.

        Raises InvalidEvidenceError for bad files, DuplicateEvidenceError for
        a hash collision within the same project (unless allow_duplicate).
        """
        if self.get_project(project_id) is None:
            raise ValueError(f"Unknown project: {project_id}")

        validate_upload(source_path, original_filename, max_upload_mb)
        file_hash = sha256_file(source_path)

        if not allow_duplicate:
            existing = self.conn.execute(
                "SELECT id FROM evidence WHERE project_id = ? AND sha256 = ?",
                (project_id, file_hash),
            ).fetchone()
            if existing is not None:
                raise DuplicateEvidenceError(existing["id"])

        evidence_id = self._next_evidence_id(project_id)
        safe_name = sanitize_filename(original_filename)
        stored_filename = f"{evidence_id}_{safe_name}"

        project_dir = self.evidence_root / project_id
        project_dir.mkdir(parents=True, exist_ok=True)
        target_path = resolve_within(project_dir, project_dir / stored_filename)

        shutil.copy2(source_path, target_path)
        target_path.chmod(0o444)  # read-only: originals are immutable

        mime_type = None
        import mimetypes as _mt

        mime_type = _mt.guess_type(original_filename)[0]

        created_at = datetime.now(UTC).isoformat()
        try:
            self.conn.execute(
                """
                INSERT INTO evidence (
                    id, project_id, original_filename, safe_filename, stored_path,
                    evidence_type, mime_type, size_bytes, sha256, created_at,
                    source_type, description, tags, host, port, finding_links,
                    verification_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence_id, project_id, original_filename, stored_filename, str(target_path),
                    evidence_type, mime_type, target_path.stat().st_size, file_hash, created_at,
                    source_type, description, json.dumps(tags or []), host, port, json.dumps([]),
                    "unverified",
                ),
            )
        except sqlite3.IntegrityError as exc:
            # Roll back the file copy if the DB insert fails for any reason
            # (e.g. a race on the UNIQUE(project_id, sha256) constraint).
            target_path.chmod(0o644)
            target_path.unlink(missing_ok=True)
            raise DuplicateEvidenceError(evidence_id) from exc

        return self.get_evidence(project_id, evidence_id)

    def get_evidence(self, project_id: str, evidence_id: str) -> Evidence | None:
        row = self.conn.execute(
            "SELECT * FROM evidence WHERE project_id = ? AND id = ?", (project_id, evidence_id)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_evidence(row)

    def list_evidence(self, project_id: str) -> list[Evidence]:
        rows = self.conn.execute(
            "SELECT * FROM evidence WHERE project_id = ? ORDER BY id", (project_id,)
        ).fetchall()
        return [self._row_to_evidence(r) for r in rows]

    def update_notes(self, project_id: str, evidence_id: str, manual_notes: str) -> None:
        self.conn.execute(
            "UPDATE evidence SET manual_notes = ? WHERE project_id = ? AND id = ?",
            (manual_notes, project_id, evidence_id),
        )

    def set_verification_status(self, project_id: str, evidence_id: str, status: str) -> None:
        self.conn.execute(
            "UPDATE evidence SET verification_status = ? WHERE project_id = ? AND id = ?",
            (status, project_id, evidence_id),
        )

    def verify_integrity(self, project_id: str, evidence_id: str) -> bool:
        """Recompute the hash of the stored file and compare to the recorded one."""
        ev = self.get_evidence(project_id, evidence_id)
        if ev is None:
            raise ValueError("Unknown evidence")
        return sha256_file(Path(ev.stored_path)) == ev.sha256

    @staticmethod
    def _row_to_evidence(row: sqlite3.Row) -> Evidence:
        d = dict(row)
        d.pop("rowid_pk", None)
        d["tags"] = json.loads(d.get("tags") or "[]")
        d["finding_links"] = json.loads(d.get("finding_links") or "[]")
        return Evidence(**d)
