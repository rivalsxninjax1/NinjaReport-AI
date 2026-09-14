"""Findings persistence.

Hard rule encoded in code, not just docs: `severity` is only ever written
by approve_severity(), which represents a human decision. suggest_severity()
(the AI-facing path) can only ever touch ai_suggested_severity /
ai_severity_rationale — there is no code path from AI output straight to
the effective severity field.

cvss_vector is free-text and only ever set verbatim by set_cvss_vector() —
nothing in this module computes or infers a CVSS score.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from core.evidence_store import EvidenceStore
from core.models import Finding, FindingStatus, Severity

VALID_SEVERITIES = {s.value for s in Severity}
VALID_STATUSES = {s.value for s in FindingStatus}


class FindingsStore:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def _next_finding_id(self, project_id: str) -> str:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute(
                "SELECT next_finding_seq FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"Unknown project: {project_id}")
            seq = row["next_finding_seq"]
            self.conn.execute(
                "UPDATE projects SET next_finding_seq = ? WHERE id = ?", (seq + 1, project_id)
            )
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        return f"FIND-{seq:03d}"

    def create_finding(
        self,
        project_id: str,
        title: str,
        affected_asset: str = "",
        description: str = "",
        technical_impact: str = "",
        business_impact: str = "",
        reproduction_steps: list[str] | None = None,
        evidence_ids: list[str] | None = None,
        remediation: str = "",
        references_list: list[str] | None = None,
        verification_notes: str = "",
        evidence_store: EvidenceStore | None = None,
    ) -> Finding:
        evidence_ids = evidence_ids or []
        if evidence_store is not None:
            _validate_evidence_ids(evidence_store, project_id, evidence_ids)

        finding_id = self._next_finding_id(project_id)
        now = datetime.now(UTC).isoformat()

        self.conn.execute(
            """
            INSERT INTO findings (
                id, project_id, title, status, affected_asset, description,
                technical_impact, business_impact, reproduction_steps, evidence_ids,
                remediation, references_list, verification_notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                finding_id, project_id, title, FindingStatus.OPEN.value, affected_asset, description,
                technical_impact, business_impact, json.dumps(reproduction_steps or []),
                json.dumps(evidence_ids), remediation, json.dumps(references_list or []),
                verification_notes, now, now,
            ),
        )
        return self.get_finding(project_id, finding_id)

    def suggest_severity(self, project_id: str, finding_id: str, severity: str, rationale: str) -> Finding:
        """AI-facing path. Only ever writes ai_suggested_severity /
        ai_severity_rationale — never the effective `severity` field."""
        _validate_severity(severity)
        self._touch(
            project_id, finding_id,
            "ai_suggested_severity = ?, ai_severity_rationale = ?",
            (severity, rationale),
        )
        return self.get_finding(project_id, finding_id)

    def approve_severity(self, project_id: str, finding_id: str, severity: str) -> Finding:
        """The only method that sets the effective severity. Represents an
        explicit human decision — the severity approved need not match
        whatever the AI suggested."""
        _validate_severity(severity)
        self._touch(
            project_id, finding_id,
            "severity = ?, severity_source = 'human_approved'",
            (severity,),
        )
        return self.get_finding(project_id, finding_id)

    def set_cvss_vector(self, project_id: str, finding_id: str, vector: str) -> Finding:
        """Stores the vector verbatim. Never computed or validated for
        correctness — transparent and fully human-editable, per the
        'never invent CVSS' rule."""
        self._touch(project_id, finding_id, "cvss_vector = ?", (vector,))
        return self.get_finding(project_id, finding_id)

    def update_status(self, project_id: str, finding_id: str, status: str) -> Finding:
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid finding status: {status}. Must be one of {VALID_STATUSES}")
        self._touch(project_id, finding_id, "status = ?", (status,))
        return self.get_finding(project_id, finding_id)

    def update_evidence_ids(
        self, project_id: str, finding_id: str, evidence_ids: list[str],
        evidence_store: EvidenceStore | None = None,
    ) -> Finding:
        if evidence_store is not None:
            _validate_evidence_ids(evidence_store, project_id, evidence_ids)
        self._touch(project_id, finding_id, "evidence_ids = ?", (json.dumps(evidence_ids),))
        return self.get_finding(project_id, finding_id)

    def _touch(self, project_id: str, finding_id: str, set_clause: str, params: tuple) -> None:
        now = datetime.now(UTC).isoformat()
        self.conn.execute(
            f"UPDATE findings SET {set_clause}, updated_at = ? WHERE project_id = ? AND id = ?",
            (*params, now, project_id, finding_id),
        )

    def get_finding(self, project_id: str, finding_id: str) -> Finding | None:
        row = self.conn.execute(
            "SELECT * FROM findings WHERE project_id = ? AND id = ?", (project_id, finding_id)
        ).fetchone()
        return self._row_to_finding(row) if row else None

    def list_findings(self, project_id: str) -> list[Finding]:
        rows = self.conn.execute(
            "SELECT * FROM findings WHERE project_id = ? ORDER BY id", (project_id,)
        ).fetchall()
        return [self._row_to_finding(r) for r in rows]

    @staticmethod
    def _row_to_finding(row: sqlite3.Row) -> Finding:
        d = dict(row)
        d.pop("rowid_pk", None)
        d["reproduction_steps"] = json.loads(d.get("reproduction_steps") or "[]")
        d["evidence_ids"] = json.loads(d.get("evidence_ids") or "[]")
        d["references_list"] = json.loads(d.get("references_list") or "[]")
        return Finding(**d)


def _validate_severity(severity: str) -> None:
    if severity not in VALID_SEVERITIES:
        raise ValueError(f"Invalid severity: {severity}. Must be one of {VALID_SEVERITIES}")


def _validate_evidence_ids(evidence_store: EvidenceStore, project_id: str, evidence_ids: list[str]) -> None:
    for evidence_id in evidence_ids:
        if evidence_store.get_evidence(project_id, evidence_id) is None:
            raise ValueError(f"Unknown evidence_id for this project: {evidence_id}")
