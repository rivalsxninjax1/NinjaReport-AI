"""Persistence for derived artifacts (previews, OCR text, PDF page text).

Kept separate from EvidenceStore: derived artifacts can be regenerated at
any time and never touch the immutable original evidence row or file.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass
class DerivedArtifact:
    id: int
    project_id: str
    evidence_id: str
    artifact_type: str
    engine: str
    created_at: str
    page_number: int | None = None
    content_path: str | None = None
    text_content: str | None = None
    confidence: float | None = None


class DerivedStore:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def add_artifact(
        self,
        project_id: str,
        evidence_id: str,
        artifact_type: str,
        engine: str,
        page_number: int | None = None,
        content_path: str | None = None,
        text_content: str | None = None,
        confidence: float | None = None,
    ) -> DerivedArtifact:
        created_at = datetime.now(UTC).isoformat()
        cur = self.conn.execute(
            """
            INSERT INTO derived_artifacts (
                project_id, evidence_id, artifact_type, page_number,
                content_path, text_content, engine, confidence, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id, evidence_id, artifact_type, page_number,
                content_path, text_content, engine, confidence, created_at,
            ),
        )
        return DerivedArtifact(
            id=cur.lastrowid, project_id=project_id, evidence_id=evidence_id,
            artifact_type=artifact_type, page_number=page_number,
            content_path=content_path, text_content=text_content,
            engine=engine, confidence=confidence, created_at=created_at,
        )

    def list_artifacts(self, project_id: str, evidence_id: str) -> list[DerivedArtifact]:
        rows = self.conn.execute(
            """
            SELECT * FROM derived_artifacts
            WHERE project_id = ? AND evidence_id = ?
            ORDER BY page_number IS NOT NULL, page_number, id
            """,
            (project_id, evidence_id),
        ).fetchall()
        return [DerivedArtifact(**dict(r)) for r in rows]

    def get_combined_text(self, project_id: str, evidence_id: str) -> str | None:
        """Concatenate all text-bearing artifacts (OCR + PDF page text) in
        page order, for use as a single searchable blob."""
        artifacts = [
            a for a in self.list_artifacts(project_id, evidence_id)
            if a.text_content
        ]
        if not artifacts:
            return None
        return "\n".join(a.text_content for a in artifacts)
