"""Plain dataclasses for Project and Evidence records.

Phase 1 keeps these dependency-free (no Pydantic yet — that lands in Phase 4
for AI-facing schemas). These mirror the SQLite schema in db.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class VerificationStatus(str, Enum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    DISPUTED = "disputed"


class SourceType(str, Enum):
    SCREENSHOT = "screenshot"
    PDF = "pdf"
    NOTE = "note"
    OTHER = "other"


@dataclass
class Project:
    id: str
    name: str
    created_at: str
    next_evidence_seq: int = 1


@dataclass
class Evidence:
    id: str  # e.g. "EVD-001", unique within its project
    project_id: str
    original_filename: str
    safe_filename: str
    stored_path: str
    evidence_type: str
    mime_type: str | None
    size_bytes: int
    sha256: str
    created_at: str
    source_type: str = SourceType.OTHER.value
    description: str = ""
    tags: list[str] = field(default_factory=list)
    host: str | None = None
    port: int | None = None
    finding_links: list[str] = field(default_factory=list)
    ocr_text: str | None = None
    ai_analysis: str | None = None
    manual_notes: str = ""
    verification_status: str = VerificationStatus.UNVERIFIED.value
