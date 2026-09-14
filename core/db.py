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

-- Derived artifacts: previews, OCR text, PDF page text. Always separate from
-- the immutable original stored in `evidence`. Phase 2.
CREATE TABLE IF NOT EXISTS derived_artifacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    artifact_type TEXT NOT NULL,  -- 'preview' | 'ocr_text' | 'pdf_page_text'
    page_number INTEGER,          -- NULL for non-paged artifacts (e.g. image OCR)
    content_path TEXT,            -- path to derived file, e.g. a preview image
    text_content TEXT,            -- extracted/OCR'd text, if applicable
    engine TEXT NOT NULL,         -- 'pillow' | 'tesseract' | 'pymupdf' | 'unavailable'
    confidence REAL,              -- 0.0-1.0, NULL when not applicable/unavailable
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id, evidence_id) REFERENCES evidence(project_id, id)
);

CREATE INDEX IF NOT EXISTS idx_derived_evidence ON derived_artifacts(project_id, evidence_id);

-- AI analysis cache, keyed on file hash + model + prompt version so identical
-- evidence is never re-analyzed unnecessarily. Phase 3.
CREATE TABLE IF NOT EXISTS ai_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_hash TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    response TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(file_hash, model, prompt_version)
);

-- Smart-crop suggestions. Never modify the original evidence file; an
-- accepted suggestion produces a separate derived cropped image. Phase 5.
CREATE TABLE IF NOT EXISTS crop_suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,  -- parent evidence this crop is derived from
    x1 INTEGER NOT NULL,
    y1 INTEGER NOT NULL,
    x2 INTEGER NOT NULL,
    y2 INTEGER NOT NULL,
    reason TEXT NOT NULL,
    confidence REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | accepted | rejected | adjusted
    derived_path TEXT,       -- set once accepted/adjusted and a crop file is generated
    created_at TEXT NOT NULL,
    decided_at TEXT,
    FOREIGN KEY (project_id, evidence_id) REFERENCES evidence(project_id, id)
);

CREATE INDEX IF NOT EXISTS idx_crop_suggestions_evidence ON crop_suggestions(project_id, evidence_id);

-- Generic relationship graph: evidence <-> hosts, services, tools, commands,
-- other evidence, report sections (added freely later — relation_type and
-- entity types are just strings, no schema migration needed to add kinds).
-- Phase 5.
CREATE TABLE IF NOT EXISTS relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    from_type TEXT NOT NULL,
    from_id TEXT NOT NULL,
    to_type TEXT NOT NULL,
    to_id TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(project_id, from_type, from_id, to_type, to_id, relation_type)
);

CREATE INDEX IF NOT EXISTS idx_relationships_from ON relationships(project_id, from_type, from_id);
CREATE INDEX IF NOT EXISTS idx_relationships_to ON relationships(project_id, to_type, to_id);
"""


def get_connection(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, isolation_level=None)  # autocommit; we manage txns explicitly
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
