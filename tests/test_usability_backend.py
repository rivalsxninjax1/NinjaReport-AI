"""Usability-pass backend tests.

Deleting evidence/findings is destructive and touches many tables, so these
tests check both directions: everything that should disappear does, and
nothing that should survive is touched (bystander evidence, unrelated
relationships, other report items).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.db import get_connection, init_schema
from core.evidence_store import EvidenceStore
from core.findings_store import FindingsStore
from processors.derived_store import DerivedStore


@pytest.fixture
def env(tmp_path):
    conn = get_connection(tmp_path / "db" / "test.sqlite3")
    init_schema(conn)
    derived_root = tmp_path / "derived"
    evidence_store = EvidenceStore(conn, evidence_root=tmp_path / "evidence")
    findings_store = FindingsStore(conn)
    derived_store = DerivedStore(conn)
    project = evidence_store.create_project("Cascade Test")

    def add_evidence(name: str, content: bytes):
        src = tmp_path / name
        src.write_bytes(content)
        return evidence_store.add_evidence(
            project_id=project.id, source_path=src, original_filename=name,
            evidence_type="screenshot", max_upload_mb=50,
        )

    target = add_evidence("target.png", b"target bytes")
    bystander = add_evidence("bystander.png", b"bystander bytes")

    # Derived data (with a real file on disk) for the target evidence.
    derived_dir = derived_root / project.id / target.id
    derived_dir.mkdir(parents=True)
    preview = derived_dir / "preview.jpg"
    preview.write_bytes(b"preview")
    derived_store.add_artifact(project.id, target.id, "preview", engine="pillow", content_path=str(preview))
    derived_store.add_artifact(project.id, target.id, "ocr_text", engine="tesseract", text_content="text")

    conn.execute(
        "INSERT INTO crop_suggestions (project_id, evidence_id, x1, y1, x2, y2, reason, confidence, status, created_at) "
        "VALUES (?, ?, 0, 0, 10, 10, 'test', 0.5, 'pending', datetime('now'))",
        (project.id, target.id),
    )
    for evidence_id, host in ((target.id, "10.0.0.5"), (bystander.id, "10.0.0.6")):
        conn.execute(
            "INSERT INTO relationships (project_id, from_type, from_id, to_type, to_id, relation_type, created_at) "
            "VALUES (?, 'evidence', ?, 'host', ?, 'observed_in', datetime('now'))",
            (project.id, evidence_id, host),
        )

    finding = findings_store.create_finding(
        project.id, title="Test finding", evidence_ids=[target.id, bystander.id], evidence_store=evidence_store,
    )
    conn.execute(
        "INSERT INTO attack_path_nodes (project_id, stage, title, evidence_ids, status, order_index, created_at, updated_at) "
        "VALUES (?, 'recon', 'Initial recon', ?, 'verified', 0, datetime('now'), datetime('now'))",
        (project.id, json.dumps([target.id])),
    )

    conn.execute(
        "INSERT INTO report_plans (project_id, template_type, created_at, updated_at) "
        "VALUES (?, 'ctf', datetime('now'), datetime('now'))",
        (project.id,),
    )
    plan_id = conn.execute("SELECT id FROM report_plans WHERE project_id = ?", (project.id,)).fetchone()["id"]
    conn.execute(
        "INSERT INTO report_sections (report_plan_id, title, order_index, included, created_at) "
        "VALUES (?, 'Evidence Appendix', 0, 1, datetime('now'))",
        (plan_id,),
    )
    section_id = conn.execute("SELECT id FROM report_sections WHERE report_plan_id = ?", (plan_id,)).fetchone()["id"]
    for kind, column, value in (
        ("evidence", "evidence_id", target.id),
        ("evidence", "evidence_id", bystander.id),
        ("finding", "finding_id", finding.id),
    ):
        conn.execute(
            f"INSERT INTO report_items (report_section_id, item_type, {column}, caption, order_index, included, created_at) "
            "VALUES (?, ?, ?, '', 0, 1, datetime('now'))",
            (section_id, kind, value),
        )

    yield {
        "conn": conn, "evidence_store": evidence_store, "findings_store": findings_store,
        "derived_store": derived_store, "derived_root": derived_root, "derived_dir": derived_dir,
        "project": project, "target": target, "bystander": bystander,
        "finding": finding, "section_id": section_id,
    }
    conn.close()


# ---------- rename ----------

def test_rename_project_updates_name(env):
    renamed = env["evidence_store"].rename_project(env["project"].id, "  Better Name  ")
    assert renamed.name == "Better Name"
    assert env["evidence_store"].get_project(env["project"].id).name == "Better Name"


def test_rename_project_rejects_blank_name(env):
    with pytest.raises(ValueError):
        env["evidence_store"].rename_project(env["project"].id, "   ")


# ---------- delete_evidence ----------

def test_delete_evidence_removes_row_file_and_derived_data(env):
    target = env["target"]
    env["evidence_store"].delete_evidence(env["project"].id, target.id, env["derived_root"])

    assert env["evidence_store"].get_evidence(env["project"].id, target.id) is None
    assert not Path(target.stored_path).exists()
    assert not env["derived_dir"].exists()
    assert env["derived_store"].list_artifacts(env["project"].id, target.id) == []


def test_delete_evidence_removes_crops_and_relationships_but_not_bystanders(env):
    conn = env["conn"]
    target, bystander = env["target"], env["bystander"]
    env["evidence_store"].delete_evidence(env["project"].id, target.id, env["derived_root"])

    crops = conn.execute("SELECT COUNT(*) AS c FROM crop_suggestions WHERE evidence_id = ?", (target.id,)).fetchone()["c"]
    assert crops == 0
    own_rels = conn.execute(
        "SELECT COUNT(*) AS c FROM relationships WHERE from_id = ? OR to_id = ?", (target.id, target.id)
    ).fetchone()["c"]
    assert own_rels == 0
    other_rels = conn.execute(
        "SELECT COUNT(*) AS c FROM relationships WHERE from_id = ?", (bystander.id,)
    ).fetchone()["c"]
    assert other_rels == 1
    assert env["evidence_store"].get_evidence(env["project"].id, bystander.id) is not None
    assert Path(bystander.stored_path).exists()


def test_delete_evidence_strips_references_from_findings_and_attack_path(env):
    conn = env["conn"]
    env["evidence_store"].delete_evidence(env["project"].id, env["target"].id, env["derived_root"])

    finding = env["findings_store"].get_finding(env["project"].id, env["finding"].id)
    assert finding.evidence_ids == [env["bystander"].id]

    node = conn.execute(
        "SELECT evidence_ids, status FROM attack_path_nodes WHERE project_id = ?", (env["project"].id,)
    ).fetchone()
    assert json.loads(node["evidence_ids"]) == []
    assert node["status"] == "unverified"  # lost its only evidence, so no longer verified


def test_delete_evidence_removes_report_items_only_for_that_evidence(env):
    conn = env["conn"]
    env["evidence_store"].delete_evidence(env["project"].id, env["target"].id, env["derived_root"])

    rows = conn.execute(
        "SELECT item_type, evidence_id FROM report_items WHERE report_section_id = ? ORDER BY id",
        (env["section_id"],),
    ).fetchall()
    assert [(r["item_type"], r["evidence_id"]) for r in rows] == [
        ("evidence", env["bystander"].id),
        ("finding", None),
    ]


def test_delete_evidence_unknown_id_raises(env):
    with pytest.raises(ValueError):
        env["evidence_store"].delete_evidence(env["project"].id, "EVD-999", env["derived_root"])


# ---------- delete_finding ----------

def test_delete_finding_removes_finding_and_its_report_items(env):
    conn = env["conn"]
    env["findings_store"].delete_finding(env["project"].id, env["finding"].id)

    assert env["findings_store"].get_finding(env["project"].id, env["finding"].id) is None
    finding_items = conn.execute(
        "SELECT COUNT(*) AS c FROM report_items WHERE item_type = 'finding'"
    ).fetchone()["c"]
    assert finding_items == 0
    evidence_items = conn.execute(
        "SELECT COUNT(*) AS c FROM report_items WHERE item_type = 'evidence'"
    ).fetchone()["c"]
    assert evidence_items == 2  # untouched


def test_delete_finding_unknown_id_raises(env):
    with pytest.raises(ValueError):
        env["findings_store"].delete_finding(env["project"].id, "FIND-999")


# ---------- connection tuning ----------

def test_connection_sets_busy_timeout(tmp_path):
    conn = get_connection(tmp_path / "t.sqlite3")
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    conn.close()
