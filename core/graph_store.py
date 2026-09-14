"""Generic relationship graph.

Entity types and relation types are plain strings by design — adding a new
kind of relationship (e.g. a finding, once Phase 6 lands) needs no schema
migration, just a new string value. Duplicate relationships are silently
ignored (idempotent) rather than erroring, since re-linking the same pair
is a common, harmless UI action.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass
class Relationship:
    id: int
    project_id: str
    from_type: str
    from_id: str
    to_type: str
    to_id: str
    relation_type: str
    created_at: str


class RelationshipStore:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def add_relationship(
        self, project_id: str, from_type: str, from_id: str,
        to_type: str, to_id: str, relation_type: str,
    ) -> None:
        created_at = datetime.now(UTC).isoformat()
        self.conn.execute(
            """
            INSERT INTO relationships
                (project_id, from_type, from_id, to_type, to_id, relation_type, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, from_type, from_id, to_type, to_id, relation_type) DO NOTHING
            """,
            (project_id, from_type, from_id, to_type, to_id, relation_type, created_at),
        )

    def remove_relationship(
        self, project_id: str, from_type: str, from_id: str,
        to_type: str, to_id: str, relation_type: str,
    ) -> None:
        self.conn.execute(
            """
            DELETE FROM relationships
            WHERE project_id = ? AND from_type = ? AND from_id = ?
              AND to_type = ? AND to_id = ? AND relation_type = ?
            """,
            (project_id, from_type, from_id, to_type, to_id, relation_type),
        )

    def list_from(self, project_id: str, from_type: str, from_id: str) -> list[Relationship]:
        rows = self.conn.execute(
            "SELECT * FROM relationships WHERE project_id = ? AND from_type = ? AND from_id = ? ORDER BY id",
            (project_id, from_type, from_id),
        ).fetchall()
        return [Relationship(**dict(r)) for r in rows]

    def list_to(self, project_id: str, to_type: str, to_id: str) -> list[Relationship]:
        rows = self.conn.execute(
            "SELECT * FROM relationships WHERE project_id = ? AND to_type = ? AND to_id = ? ORDER BY id",
            (project_id, to_type, to_id),
        ).fetchall()
        return [Relationship(**dict(r)) for r in rows]
