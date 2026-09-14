"""Attack path persistence.

Hard rule encoded in code: a node's `status` is always computed from
whether it has evidence attached — 'verified' iff evidence_ids is
non-empty, 'unverified' otherwise. There is no method that lets a caller
set status directly; the only way to become verified is to attach real,
existing evidence.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from core.evidence_store import EvidenceStore
from core.models import ATTACK_STAGE_ORDER, AttackPathNode, VerificationStatus

STAGE_INDEX = {stage: i for i, stage in enumerate(ATTACK_STAGE_ORDER)}


def _validate_stage(stage: str) -> None:
    if stage not in STAGE_INDEX:
        raise ValueError(f"Invalid attack stage: {stage}. Must be one of {ATTACK_STAGE_ORDER}")


def _validate_evidence_ids(evidence_store: EvidenceStore, project_id: str, evidence_ids: list[str]) -> None:
    for evidence_id in evidence_ids:
        if evidence_store.get_evidence(project_id, evidence_id) is None:
            raise ValueError(f"Unknown evidence_id for this project: {evidence_id}")


def _compute_status(evidence_ids: list[str]) -> str:
    return VerificationStatus.VERIFIED.value if evidence_ids else VerificationStatus.UNVERIFIED.value


class AttackPathStore:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def add_node(
        self,
        project_id: str,
        stage: str,
        title: str,
        description: str = "",
        evidence_ids: list[str] | None = None,
        evidence_store: EvidenceStore | None = None,
    ) -> AttackPathNode:
        _validate_stage(stage)
        evidence_ids = evidence_ids or []
        if evidence_store is not None:
            _validate_evidence_ids(evidence_store, project_id, evidence_ids)

        now = datetime.now(UTC).isoformat()
        status = _compute_status(evidence_ids)
        order_index = STAGE_INDEX[stage]

        cur = self.conn.execute(
            """
            INSERT INTO attack_path_nodes
                (project_id, stage, title, description, evidence_ids, status, order_index, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (project_id, stage, title, description, json.dumps(evidence_ids), status, order_index, now, now),
        )
        return self.get_node(cur.lastrowid)

    def update_evidence(
        self, node_id: int, evidence_ids: list[str], evidence_store: EvidenceStore | None = None,
    ) -> AttackPathNode:
        node = self.get_node(node_id)
        if node is None:
            raise ValueError(f"Unknown attack path node: {node_id}")
        if evidence_store is not None:
            _validate_evidence_ids(evidence_store, node.project_id, evidence_ids)

        status = _compute_status(evidence_ids)
        now = datetime.now(UTC).isoformat()
        self.conn.execute(
            "UPDATE attack_path_nodes SET evidence_ids = ?, status = ?, updated_at = ? WHERE id = ?",
            (json.dumps(evidence_ids), status, now, node_id),
        )
        return self.get_node(node_id)

    def get_node(self, node_id: int) -> AttackPathNode | None:
        row = self.conn.execute(
            "SELECT * FROM attack_path_nodes WHERE id = ?", (node_id,)
        ).fetchone()
        return self._row_to_node(row) if row else None

    def list_nodes(self, project_id: str) -> list[AttackPathNode]:
        rows = self.conn.execute(
            "SELECT * FROM attack_path_nodes WHERE project_id = ? ORDER BY order_index, id",
            (project_id,),
        ).fetchall()
        return [self._row_to_node(r) for r in rows]

    @staticmethod
    def _row_to_node(row: sqlite3.Row) -> AttackPathNode:
        d = dict(row)
        d["evidence_ids"] = json.loads(d.get("evidence_ids") or "[]")
        return AttackPathNode(**d)
