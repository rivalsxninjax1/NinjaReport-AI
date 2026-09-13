"""Phase 1 acceptance tests: evidence core."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.db import get_connection, init_schema
from core.evidence_store import DuplicateEvidenceError, EvidenceStore
from core.safe_files import InvalidEvidenceError, sanitize_filename


@pytest.fixture
def store(tmp_path):
    conn = get_connection(tmp_path / "db" / "test.sqlite3")
    init_schema(conn)
    s = EvidenceStore(conn, evidence_root=tmp_path / "evidence")
    yield s
    conn.close()


def _make_upload(tmp_path: Path, name: str, content: bytes = b"evidence bytes") -> Path:
    src_dir = tmp_path / "incoming"
    src_dir.mkdir(exist_ok=True)
    p = src_dir / "upload_tmp_file"
    p.write_bytes(content)
    return p


# ---------- Path traversal ----------

@pytest.mark.parametrize(
    "malicious_name",
    [
        "../../etc/passwd",
        "..\\..\\Windows\\System32\\config",
        "../../../root/.ssh/id_rsa",
        "/etc/shadow",
        "....//....//etc/passwd",
    ],
)
def test_sanitize_filename_neutralizes_traversal(malicious_name):
    safe = sanitize_filename(malicious_name)
    assert "/" not in safe
    assert "\\" not in safe
    assert ".." not in safe


def test_add_evidence_stores_traversal_filename_safely(store, tmp_path):
    src = _make_upload(tmp_path, "x")
    ev = store.add_evidence(
        project_id=store.create_project("Test Project").id,
        source_path=src,
        original_filename="../../etc/passwd",
        evidence_type="note",
        max_upload_mb=50,
    )
    stored_path = Path(ev.stored_path)
    # Must land inside the evidence root, never above it.
    assert stored_path.resolve().is_relative_to((tmp_path / "evidence").resolve())
    assert ".." not in ev.safe_filename


# ---------- Invalid files ----------

def test_disallowed_extension_rejected(store, tmp_path):
    project = store.create_project("Test Project")
    src = _make_upload(tmp_path, "x")
    with pytest.raises(InvalidEvidenceError):
        store.add_evidence(
            project_id=project.id,
            source_path=src,
            original_filename="payload.sh",
            evidence_type="note",
            max_upload_mb=50,
        )


def test_oversized_file_rejected(store, tmp_path):
    project = store.create_project("Test Project")
    src = _make_upload(tmp_path, "big", content=b"x" * 2048)
    with pytest.raises(InvalidEvidenceError):
        store.add_evidence(
            project_id=project.id,
            source_path=src,
            original_filename="big.png",
            evidence_type="screenshot",
            max_upload_mb=0,  # 0 MB limit -> anything nonzero exceeds it
        )


def test_empty_file_rejected(store, tmp_path):
    project = store.create_project("Test Project")
    src = _make_upload(tmp_path, "empty", content=b"")
    with pytest.raises(InvalidEvidenceError):
        store.add_evidence(
            project_id=project.id,
            source_path=src,
            original_filename="empty.png",
            evidence_type="screenshot",
            max_upload_mb=50,
        )


def test_missing_source_file_rejected(store, tmp_path):
    project = store.create_project("Test Project")
    with pytest.raises(InvalidEvidenceError):
        store.add_evidence(
            project_id=project.id,
            source_path=tmp_path / "does_not_exist.png",
            original_filename="ghost.png",
            evidence_type="screenshot",
            max_upload_mb=50,
        )


# ---------- Duplicate detection ----------

def test_duplicate_content_rejected_within_project(store, tmp_path):
    project = store.create_project("Test Project")
    src1 = _make_upload(tmp_path, "a", content=b"same bytes")
    store.add_evidence(
        project_id=project.id, source_path=src1, original_filename="a.png",
        evidence_type="screenshot", max_upload_mb=50,
    )
    src2 = _make_upload(tmp_path, "b", content=b"same bytes")
    with pytest.raises(DuplicateEvidenceError):
        store.add_evidence(
            project_id=project.id, source_path=src2, original_filename="b.png",
            evidence_type="screenshot", max_upload_mb=50,
        )


def test_duplicate_allowed_across_different_projects(store, tmp_path):
    p1 = store.create_project("Project One")
    p2 = store.create_project("Project Two")
    src1 = _make_upload(tmp_path, "a", content=b"shared bytes")
    ev1 = store.add_evidence(
        project_id=p1.id, source_path=src1, original_filename="a.png",
        evidence_type="screenshot", max_upload_mb=50,
    )
    src2 = _make_upload(tmp_path, "b", content=b"shared bytes")
    ev2 = store.add_evidence(
        project_id=p2.id, source_path=src2, original_filename="b.png",
        evidence_type="screenshot", max_upload_mb=50,
    )
    assert ev1.sha256 == ev2.sha256
    assert ev1.project_id != ev2.project_id


# ---------- Hash integrity ----------

def test_hash_integrity_detects_tampering(store, tmp_path):
    project = store.create_project("Test Project")
    src = _make_upload(tmp_path, "a", content=b"original bytes")
    ev = store.add_evidence(
        project_id=project.id, source_path=src, original_filename="a.png",
        evidence_type="screenshot", max_upload_mb=50,
    )
    assert store.verify_integrity(project.id, ev.id) is True

    # Simulate tampering with the stored (supposedly immutable) copy.
    stored_path = Path(ev.stored_path)
    stored_path.chmod(0o644)
    stored_path.write_bytes(b"tampered bytes")
    assert store.verify_integrity(project.id, ev.id) is False


def test_stored_originals_are_read_only(store, tmp_path):
    project = store.create_project("Test Project")
    src = _make_upload(tmp_path, "a")
    ev = store.add_evidence(
        project_id=project.id, source_path=src, original_filename="a.png",
        evidence_type="screenshot", max_upload_mb=50,
    )
    mode = Path(ev.stored_path).stat().st_mode
    assert not (mode & 0o222), "stored evidence should not be writable"


# ---------- Sequential IDs + persistence ----------

def test_evidence_ids_are_sequential_per_project(store, tmp_path):
    project = store.create_project("Test Project")
    ids = []
    for i in range(3):
        src = _make_upload(tmp_path, f"f{i}", content=f"content {i}".encode())
        ev = store.add_evidence(
            project_id=project.id, source_path=src, original_filename=f"f{i}.png",
            evidence_type="screenshot", max_upload_mb=50,
        )
        ids.append(ev.id)
    assert ids == ["EVD-001", "EVD-002", "EVD-003"]


def test_evidence_persists_across_new_connection(tmp_path):
    db_path = tmp_path / "db" / "test.sqlite3"
    conn1 = get_connection(db_path)
    init_schema(conn1)
    store1 = EvidenceStore(conn1, evidence_root=tmp_path / "evidence")
    project = store1.create_project("Persist Test")
    src = _make_upload(tmp_path, "a")
    ev = store1.add_evidence(
        project_id=project.id, source_path=src, original_filename="a.png",
        evidence_type="screenshot", max_upload_mb=50,
    )
    conn1.close()

    conn2 = get_connection(db_path)
    store2 = EvidenceStore(conn2, evidence_root=tmp_path / "evidence")
    fetched = store2.get_evidence(project.id, ev.id)
    assert fetched is not None
    assert fetched.sha256 == ev.sha256
    assert fetched.original_filename == "a.png"
    conn2.close()


def test_tags_and_notes_round_trip(store, tmp_path):
    project = store.create_project("Test Project")
    src = _make_upload(tmp_path, "a")
    ev = store.add_evidence(
        project_id=project.id, source_path=src, original_filename="a.png",
        evidence_type="screenshot", max_upload_mb=50, tags=["nmap", "recon"],
    )
    assert ev.tags == ["nmap", "recon"]
    store.update_notes(project.id, ev.id, "Confirmed open port 22")
    updated = store.get_evidence(project.id, ev.id)
    assert updated.manual_notes == "Confirmed open port 22"
