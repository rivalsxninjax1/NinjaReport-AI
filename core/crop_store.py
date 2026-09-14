"""Persistence for smart-crop suggestions and the human decision on each.

A suggestion always starts 'pending'. Accepting or adjusting one generates
a derived cropped image file (via processors.crop_suggester.apply_crop) —
the original evidence file is never touched, regardless of decision.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from processors.crop_suggester import apply_crop


@dataclass
class CropSuggestionRecord:
    id: int
    project_id: str
    evidence_id: str
    x1: int
    y1: int
    x2: int
    y2: int
    reason: str
    confidence: float
    status: str
    created_at: str
    derived_path: str | None = None
    decided_at: str | None = None


class CropStore:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def add_suggestion(
        self, project_id: str, evidence_id: str,
        x1: int, y1: int, x2: int, y2: int, reason: str, confidence: float,
    ) -> CropSuggestionRecord:
        created_at = datetime.now(UTC).isoformat()
        cur = self.conn.execute(
            """
            INSERT INTO crop_suggestions
                (project_id, evidence_id, x1, y1, x2, y2, reason, confidence, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (project_id, evidence_id, x1, y1, x2, y2, reason, confidence, created_at),
        )
        return self.get_suggestion(cur.lastrowid)

    def get_suggestion(self, suggestion_id: int) -> CropSuggestionRecord | None:
        row = self.conn.execute(
            "SELECT * FROM crop_suggestions WHERE id = ?", (suggestion_id,)
        ).fetchone()
        return CropSuggestionRecord(**dict(row)) if row else None

    def list_suggestions(self, project_id: str, evidence_id: str) -> list[CropSuggestionRecord]:
        rows = self.conn.execute(
            "SELECT * FROM crop_suggestions WHERE project_id = ? AND evidence_id = ? ORDER BY id",
            (project_id, evidence_id),
        ).fetchall()
        return [CropSuggestionRecord(**dict(r)) for r in rows]

    def reject(self, suggestion_id: int) -> CropSuggestionRecord:
        decided_at = datetime.now(UTC).isoformat()
        self.conn.execute(
            "UPDATE crop_suggestions SET status = 'rejected', decided_at = ? WHERE id = ?",
            (decided_at, suggestion_id),
        )
        return self.get_suggestion(suggestion_id)

    def accept(
        self, suggestion_id: int, source_path: Path, derived_root: Path,
        coords: tuple[int, int, int, int] | None = None,
    ) -> CropSuggestionRecord:
        """Accept a suggestion (optionally with adjusted coordinates) and
        generate the derived cropped file. Status becomes 'accepted' if the
        original coordinates were used, or 'adjusted' if coords overrides
        them."""
        suggestion = self.get_suggestion(suggestion_id)
        if suggestion is None:
            raise ValueError(f"Unknown crop suggestion: {suggestion_id}")

        final_coords = coords or (suggestion.x1, suggestion.y1, suggestion.x2, suggestion.y2)
        status = "adjusted" if coords is not None else "accepted"

        dest_path = derived_root / suggestion.project_id / suggestion.evidence_id / f"crop_{suggestion_id}.jpg"
        apply_crop(source_path, final_coords, dest_path)

        decided_at = datetime.now(UTC).isoformat()
        self.conn.execute(
            """
            UPDATE crop_suggestions
            SET status = ?, x1 = ?, y1 = ?, x2 = ?, y2 = ?, derived_path = ?, decided_at = ?
            WHERE id = ?
            """,
            (status, *final_coords, str(dest_path), decided_at, suggestion_id),
        )
        return self.get_suggestion(suggestion_id)
