"""Cache for AI analysis results.

Keyed on (file_hash, model, prompt_version) so re-running analysis on the
same evidence with the same model and prompt is a cache hit. Any change to
the prompt should bump prompt_version so stale cached results aren't
mistaken for fresh ones.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime


class AnalysisCache:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get(self, file_hash: str, model: str, prompt_version: str) -> dict | None:
        row = self.conn.execute(
            "SELECT response FROM ai_cache WHERE file_hash = ? AND model = ? AND prompt_version = ?",
            (file_hash, model, prompt_version),
        ).fetchone()
        if row is None:
            return None
        return json.loads(row["response"])

    def set(self, file_hash: str, model: str, prompt_version: str, response: dict) -> None:
        created_at = datetime.now(UTC).isoformat()
        self.conn.execute(
            """
            INSERT INTO ai_cache (file_hash, model, prompt_version, response, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(file_hash, model, prompt_version)
            DO UPDATE SET response = excluded.response, created_at = excluded.created_at
            """,
            (file_hash, model, prompt_version, json.dumps(response), created_at),
        )
