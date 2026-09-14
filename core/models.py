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


class Severity(str, Enum):
    INFORMATIONAL = "informational"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FindingStatus(str, Enum):
    OPEN = "open"
    IN_REVIEW = "in_review"
    REMEDIATED = "remediated"
    ACCEPTED_RISK = "accepted_risk"
    FALSE_POSITIVE = "false_positive"


class AttackStage(str, Enum):
    RECON = "recon"
    PORT_DISCOVERY = "port_discovery"
    SERVICE_ENUMERATION = "service_enumeration"
    WEB_ENUMERATION = "web_enumeration"
    VULNERABILITY_DISCOVERY = "vulnerability_discovery"
    INITIAL_ACCESS = "initial_access"
    PRIVILEGE_ESCALATION = "privilege_escalation"
    OBJECTIVE_FLAG = "objective_flag"


# Fixed kill-chain order — attack path nodes sort by this, not creation time.
ATTACK_STAGE_ORDER = [s.value for s in AttackStage]


@dataclass
class Project:
    id: str
    name: str
    created_at: str
    next_evidence_seq: int = 1
    next_finding_seq: int = 1


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


@dataclass
class Finding:
    id: str  # e.g. "FIND-001", unique within its project
    project_id: str
    title: str
    created_at: str
    updated_at: str
    status: str = FindingStatus.OPEN.value
    affected_asset: str = ""
    description: str = ""
    technical_impact: str = ""
    business_impact: str = ""
    reproduction_steps: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    remediation: str = ""
    references_list: list[str] = field(default_factory=list)
    verification_notes: str = ""
    severity: str | None = None              # set only via FindingsStore.approve_severity
    severity_source: str | None = None       # 'human_approved' once severity is set
    ai_suggested_severity: str | None = None  # advisory only
    ai_severity_rationale: str | None = None
    cvss_vector: str | None = None           # free-text, human-edited only


@dataclass
class AttackPathNode:
    id: int
    project_id: str
    stage: str
    title: str
    created_at: str
    updated_at: str
    description: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    status: str = VerificationStatus.UNVERIFIED.value  # always computed, never set directly
    order_index: int = 0
