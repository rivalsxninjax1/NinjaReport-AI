"""Pydantic schemas for AI-generated evidence analysis.

Hard rules encoded here, not just documented:
  - verification_status is ALWAYS forced to "unverified" on construction,
    regardless of what the model returns. Only a human reviewer (Phase 6
    findings workflow) can promote evidence to verified/disputed. The AI
    cannot self-certify its own output.
  - Every TechnicalFact requires a non-empty `source` — a factual claim
    with no traceable origin in the evidence text is not allowed to exist.
  - Ports are range-checked (0-65535); malformed values fail validation
    and trigger the schema-repair path in evidence_analyzer.py rather than
    being silently accepted.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class TechnicalFact(BaseModel):
    fact: str = Field(..., min_length=1)
    confidence: float = Field(..., ge=0.0, le=1.0)
    source: str = Field(..., min_length=1)  # e.g. "OCR text, line 3" or "PDF page 2"


class EvidenceAnalysis(BaseModel):
    evidence_id: str = Field(..., min_length=1)
    classification: str = Field(..., min_length=1)
    summary: str = Field(..., min_length=1)

    technical_facts: list[TechnicalFact] = Field(default_factory=list)
    commands: list[str] = Field(default_factory=list)
    ips: list[str] = Field(default_factory=list)
    ports: list[int] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)
    usernames: list[str] = Field(default_factory=list)
    credentials: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    security_observations: list[str] = Field(default_factory=list)

    suggested_report_section: str = ""
    suggested_caption: str = ""
    corrected_text: str | None = None  # AI's best-effort OCR correction; still unverified

    verification_status: str = "unverified"
    uncertainties: list[str] = Field(default_factory=list)

    @field_validator("verification_status", mode="before")
    @classmethod
    def force_unverified(cls, _value: object) -> str:
        """No matter what the model outputs here, evidence analysis starts
        unverified. This is not a default — it's a floor."""
        return "unverified"

    @field_validator("ports")
    @classmethod
    def validate_port_range(cls, ports: list[int]) -> list[int]:
        for port in ports:
            if not (0 <= port <= 65535):
                raise ValueError(f"Port {port} is outside the valid range 0-65535")
        return ports
