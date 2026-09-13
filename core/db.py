"""SQLite connection and schema setup.

Uses stdlib sqlite3 directly (no ORM) per the "least code necessary"
principle — the schema is small and stable.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    next_evidence_seq INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS evidence (
    rowid_pk INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id),
    original_filename TEXT NOT NULL,
    safe_filename TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    evidence_type TEXT NOT NULL,
    mime_type TEXT,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'other',
    description TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    host TEXT,
    port INTEGER,
    finding_links TEXT NOT NULL DEFAULT '[]',
    ocr_text TEXT,
    ai_analysis TEXT,
    manual_notes TEXT NOT NULL DEFAULT '',
    verification_status TEXT NOT NULL DEFAULT 'unverified',
    UNIQUE(project_id, id),
    UNIQUE(project_id, sha256)
);

CREATE INDEX IF NOT EXISTS idx_evidence_project ON evidence(project_id);
"""


def get_connection(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, isolation_level=None)  # autocommit; we manage txns explicitly
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
