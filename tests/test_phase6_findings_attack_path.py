"""Phase 6 acceptance tests: findings + attack path.

Pure stdlib + SQLite — fully executable in any environment.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.attack_path_store import AttackPathStore
from core.db import get_connection, init_schema
from core.evidence_store import EvidenceStore
from core.findings_store import FindingsStore


@pytest.fixture
def env(tmp_path):
    conn = get_connection(tmp_path / "db" / "test.sqlite3")
    init_schema(conn)
    evidence_store = EvidenceStore(conn, evidence_root=tmp_path / "evidence")
    findings_store = FindingsStore(conn)
    attack_path_store = AttackPathStore(conn)

    project = evidence_store.create_project("Phase 6 Test")

    src = tmp_path / "scan.txt"
    src.write_bytes(b"nmap scan output")
    evidence = evidence_store.add_evidence(
        project_id=project.id, source_path=src, original_filename="scan.txt",
        evidence_type="note", max_upload_mb=50,
    )

    yield {
        "conn": conn, "evidence_store": evidence_store, "findings_store": findings_store,
        "attack_path_store": attack_path_store, "project": project, "evidence": evidence,
    }
    conn.close()


# ---------- Migration ----------

def test_projects_table_migrated_with_next_finding_seq(env):
    project = env["evidence_store"].get_project(env["project"].id)
    assert project.next_finding_seq == 1


def test_migration_is_idempotent_on_existing_database(tmp_path):
    db_path = tmp_path / "db" / "test.sqlite3"
    conn1 = get_connection(db_path)
    init_schema(conn1)
    conn1.close()

    # Re-running init_schema against an already-migrated DB must not error.
    conn2 = get_connection(db_path)
    init_schema(conn2)  # would raise "duplicate column" if the guard were missing
    conn2.close()


# ---------- Sequential finding IDs ----------

def test_finding_ids_are_sequential_per_project(env):
    findings_store: FindingsStore = env["findings_store"]
    project = env["project"]

    f1 = findings_store.create_finding(project.id, title="Open SSH port")
    f2 = findings_store.create_finding(project.id, title="Weak credentials")
    assert f1.id == "FIND-001"
    assert f2.id == "FIND-002"


def test_finding_starts_open_with_no_severity(env):
    findings_store: FindingsStore = env["findings_store"]
    finding = findings_store.create_finding(env["project"].id, title="Test finding")
    assert finding.status == "open"
    assert finding.severity is None
    assert finding.severity_source is None


# ---------- Human-only severity approval ----------

def test_ai_suggestion_never_sets_effective_severity(env):
    findings_store: FindingsStore = env["findings_store"]
    project = env["project"]
    finding = findings_store.create_finding(project.id, title="Test finding")

    updated = findings_store.suggest_severity(project.id, finding.id, "critical", "Remote code execution possible")
    assert updated.ai_suggested_severity == "critical"
    assert updated.ai_severity_rationale == "Remote code execution possible"
    assert updated.severity is None  # NOT set by the AI suggestion
    assert updated.severity_source is None


def test_human_approval_sets_effective_severity(env):
    findings_store: FindingsStore = env["findings_store"]
    project = env["project"]
    finding = findings_store.create_finding(project.id, title="Test finding")
    findings_store.suggest_severity(project.id, finding.id, "critical", "rationale")

    approved = findings_store.approve_severity(project.id, finding.id, "high")
    assert approved.severity == "high"  # human can override the AI suggestion
    assert approved.severity_source == "human_approved"
    assert approved.ai_suggested_severity == "critical"  # original suggestion preserved for audit


def test_invalid_severity_rejected(env):
    findings_store: FindingsStore = env["findings_store"]
    project = env["project"]
    finding = findings_store.create_finding(project.id, title="Test finding")
    with pytest.raises(ValueError):
        findings_store.approve_severity(project.id, finding.id, "super-critical")
    with pytest.raises(ValueError):
        findings_store.suggest_severity(project.id, finding.id, "super-critical", "x")


# ---------- CVSS: transparent, never computed ----------

def test_cvss_vector_stored_verbatim_and_editable(env):
    findings_store: FindingsStore = env["findings_store"]
    project = env["project"]
    finding = findings_store.create_finding(project.id, title="Test finding")

    updated = findings_store.set_cvss_vector(project.id, finding.id, "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H")
    assert updated.cvss_vector == "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"

    edited = findings_store.set_cvss_vector(project.id, finding.id, "CVSS:3.1/AV:N/AC:H/PR:N/UI:N/S:U/C:L/I:L/A:N")
    assert edited.cvss_vector == "CVSS:3.1/AV:N/AC:H/PR:N/UI:N/S:U/C:L/I:L/A:N"


def test_finding_never_auto_generates_cvss(env):
    findings_store: FindingsStore = env["findings_store"]
    finding = findings_store.create_finding(env["project"].id, title="Test finding")
    assert finding.cvss_vector is None  # nothing computes a default


# ---------- Evidence linkage validation ----------

def test_create_finding_rejects_unknown_evidence_id(env):
    findings_store: FindingsStore = env["findings_store"]
    with pytest.raises(ValueError):
        findings_store.create_finding(
            env["project"].id, title="Test", evidence_ids=["EVD-999"],
            evidence_store=env["evidence_store"],
        )


def test_create_finding_accepts_real_evidence_id(env):
    findings_store: FindingsStore = env["findings_store"]
    project = env["project"]
    evidence = env["evidence"]
    finding = findings_store.create_finding(
        project.id, title="Test", evidence_ids=[evidence.id],
        evidence_store=env["evidence_store"],
    )
    assert finding.evidence_ids == [evidence.id]


def test_reproduction_steps_and_references_round_trip(env):
    findings_store: FindingsStore = env["findings_store"]
    finding = findings_store.create_finding(
        env["project"].id, title="Test",
        reproduction_steps=["Run nmap -sV target", "Observe open port 22"],
        references_list=["https://nvd.nist.gov/vuln/example"],
    )
    assert finding.reproduction_steps == ["Run nmap -sV target", "Observe open port 22"]
    assert finding.references_list == ["https://nvd.nist.gov/vuln/example"]


# ---------- Attack path: evidence-backed verification ----------

def test_node_without_evidence_is_unverified(env):
    attack_path_store: AttackPathStore = env["attack_path_store"]
    node = attack_path_store.add_node(env["project"].id, stage="recon", title="Initial recon")
    assert node.status == "unverified"


def test_node_with_evidence_is_verified(env):
    attack_path_store: AttackPathStore = env["attack_path_store"]
    project = env["project"]
    evidence = env["evidence"]
    node = attack_path_store.add_node(
        project.id, stage="recon", title="Initial recon",
        evidence_ids=[evidence.id], evidence_store=env["evidence_store"],
    )
    assert node.status == "verified"


def test_removing_evidence_reverts_node_to_unverified(env):
    attack_path_store: AttackPathStore = env["attack_path_store"]
    project = env["project"]
    evidence = env["evidence"]
    node = attack_path_store.add_node(
        project.id, stage="recon", title="Initial recon",
        evidence_ids=[evidence.id], evidence_store=env["evidence_store"],
    )
    assert node.status == "verified"

    updated = attack_path_store.update_evidence(node.id, evidence_ids=[])
    assert updated.status == "unverified"


def test_add_node_rejects_invalid_stage(env):
    attack_path_store: AttackPathStore = env["attack_path_store"]
    with pytest.raises(ValueError):
        attack_path_store.add_node(env["project"].id, stage="not_a_real_stage", title="x")


def test_add_node_rejects_unknown_evidence_id(env):
    attack_path_store: AttackPathStore = env["attack_path_store"]
    with pytest.raises(ValueError):
        attack_path_store.add_node(
            env["project"].id, stage="recon", title="x",
            evidence_ids=["EVD-999"], evidence_store=env["evidence_store"],
        )


def test_nodes_list_in_fixed_kill_chain_order(env):
    attack_path_store: AttackPathStore = env["attack_path_store"]
    project = env["project"]

    # Insert deliberately out of chronological/kill-chain order.
    attack_path_store.add_node(project.id, stage="privilege_escalation", title="Escalate to root")
    attack_path_store.add_node(project.id, stage="recon", title="Initial recon")
    attack_path_store.add_node(project.id, stage="port_discovery", title="Nmap scan")

    nodes = attack_path_store.list_nodes(project.id)
    stages_in_order = [n.stage for n in nodes]
    assert stages_in_order == ["recon", "port_discovery", "privilege_escalation"]
