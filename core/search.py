"""Evidence search and filtering.

Kept intentionally simple (filter in Python, not SQL) — CTF/VAPT evidence
sets are small enough (dozens to low hundreds of files) that this is both
correct and fast, and it avoids duplicate-row headaches from joining
evidence against a variable number of derived-text artifacts.
"""
from __future__ import annotations

from core.evidence_store import EvidenceStore
from core.models import Evidence
from processors.derived_store import DerivedStore


def search_evidence(
    evidence_store: EvidenceStore,
    derived_store: DerivedStore,
    project_id: str,
    query: str | None = None,
    tags: list[str] | None = None,
    verification_status: str | None = None,
    evidence_type: str | None = None,
) -> list[Evidence]:
    results = []
    for evidence in evidence_store.list_evidence(project_id):
        if verification_status and evidence.verification_status != verification_status:
            continue
        if evidence_type and evidence.evidence_type != evidence_type:
            continue
        if tags and not set(tags).issubset(set(evidence.tags)):
            continue
        if query:
            haystack = " ".join([
                evidence.original_filename,
                evidence.description,
                evidence.manual_notes,
                derived_store.get_combined_text(project_id, evidence.id) or "",
            ]).lower()
            if query.lower() not in haystack:
                continue
        results.append(evidence)
    return results
