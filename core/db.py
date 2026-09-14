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

-- Findings. Note: `severity` is only ever set by FindingsStore.approve_severity()
-- (a human action) — ai_suggested_severity is purely advisory storage.
-- `cvss_vector` is free-text and human-edited; nothing computes it. Phase 6.
CREATE TABLE IF NOT EXISTS findings (
    rowid_pk INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id),
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    affected_asset TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    technical_impact TEXT NOT NULL DEFAULT '',
    business_impact TEXT NOT NULL DEFAULT '',
    reproduction_steps TEXT NOT NULL DEFAULT '[]',
    evidence_ids TEXT NOT NULL DEFAULT '[]',
    remediation TEXT NOT NULL DEFAULT '',
    references_list TEXT NOT NULL DEFAULT '[]',
    verification_notes TEXT NOT NULL DEFAULT '',
    severity TEXT,                  -- NULL until a human approves one
    severity_source TEXT,           -- 'human_approved' once severity is set
    ai_suggested_severity TEXT,     -- advisory only; never the effective severity
    ai_severity_rationale TEXT,
    cvss_vector TEXT,               -- free-text, human-entered/edited only
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, id)
);

CREATE INDEX IF NOT EXISTS idx_findings_project ON findings(project_id);

-- Attack path nodes across the fixed 8-stage kill chain. `status` is always
-- computed (verified iff evidence_ids is non-empty) — there is no code path
-- that lets a node be marked verified without evidence. Phase 6.
CREATE TABLE IF NOT EXISTS attack_path_nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    stage TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    evidence_ids TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'unverified',
    order_index INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_attack_path_project ON attack_path_nodes(project_id);

-- Report builder: one active plan per project, bootstrapped from a CTF or
-- VAPT template's section list. Sections and items are reorderable and
-- individually includable/excludable. No AI writing happens here — that's
-- Phase 8; this is purely the structured plan. Phase 7.
CREATE TABLE IF NOT EXISTS report_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL UNIQUE REFERENCES projects(id),
    template_type TEXT NOT NULL,  -- 'ctf' | 'vapt'
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS report_sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_plan_id INTEGER NOT NULL REFERENCES report_plans(id),
    title TEXT NOT NULL,
    order_index INTEGER NOT NULL,
    included INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_report_sections_plan ON report_sections(report_plan_id);

CREATE TABLE IF NOT EXISTS report_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_section_id INTEGER NOT NULL REFERENCES report_sections(id),
    item_type TEXT NOT NULL,  -- 'evidence' | 'finding' | 'text'
    evidence_id TEXT,         -- set when item_type = 'evidence'
    finding_id TEXT,          -- set when item_type = 'finding'
    text_content TEXT,        -- set when item_type = 'text'
    caption TEXT NOT NULL DEFAULT '',
    order_index INTEGER NOT NULL,
    included INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_report_items_section ON report_items(report_section_id);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """Guarded schema evolution for columns added to already-existing tables.

    `CREATE TABLE IF NOT EXISTS` is a no-op once a table exists — it will
    NOT add new columns to a database created by an earlier phase. Any
    phase that adds a column to `projects`, `evidence`, etc. must add a
    guarded ALTER TABLE here instead, checked against PRAGMA table_info so
    it's safe to run on every startup.
    """
    project_columns = {row["name"] for row in conn.execute("PRAGMA table_info(projects)").fetchall()}
    if "next_finding_seq" not in project_columns:
        conn.execute("ALTER TABLE projects ADD COLUMN next_finding_seq INTEGER NOT NULL DEFAULT 1")


def get_connection(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, isolation_level=None)  # autocommit; we manage txns explicitly
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _migrate(conn)
