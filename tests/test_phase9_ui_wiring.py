"""Phase 9 acceptance tests: store wiring + upload handling used by the
Streamlit pages. Streamlit itself is not exercised here (not installed in
this environment) — these test the pure-Python logic every page relies on.
"""
from __future__ import annotations

import pytest

from ui.common import build_stores, save_uploaded_bytes


@pytest.fixture
def settings(tmp_path, monkeypatch):
    for key in ("OLLAMA_TEXT_MODEL", "OLLAMA_VISION_MODEL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("NINJAREPORT_DATA_DIR", str(tmp_path / "appdata"))
    from config import Settings
    return Settings.load(env_path=tmp_path / "nonexistent.env")


def test_build_stores_wires_every_store(settings):
    stores = build_stores(settings)
    try:
        project = stores.evidence_store.create_project("Test")
        assert project.id.startswith("PRJ-")
        # Spot-check a couple of other stores share the same connection/schema.
        assert stores.findings_store.list_findings(project.id) == []
        assert stores.report_store.get_plan(project.id) is None
    finally:
        stores.conn.close()


def test_build_stores_creates_data_directories(settings):
    stores = build_stores(settings)
    try:
        assert settings.data_dir.exists()
        assert (settings.data_dir / "evidence").exists()
    finally:
        stores.conn.close()


def test_save_uploaded_bytes_writes_content(tmp_path):
    dest = save_uploaded_bytes(b"hello evidence", "screenshot.png", tmp_path / "scratch")
    assert dest.exists()
    assert dest.read_bytes() == b"hello evidence"


def test_save_uploaded_bytes_sanitizes_traversal_filename(tmp_path):
    dest = save_uploaded_bytes(b"data", "../../etc/passwd", tmp_path / "scratch")
    assert ".." not in dest.name
    assert "/" not in dest.name
    assert dest.exists()


def test_save_uploaded_bytes_unique_per_call(tmp_path):
    dest1 = save_uploaded_bytes(b"a", "same_name.png", tmp_path / "scratch")
    dest2 = save_uploaded_bytes(b"b", "same_name.png", tmp_path / "scratch")
    assert dest1 != dest2
    assert dest1.exists() and dest2.exists()


def test_uploaded_file_flows_into_evidence_store(settings, tmp_path):
    stores = build_stores(settings)
    try:
        project = stores.evidence_store.create_project("Upload Test")
        dest = save_uploaded_bytes(b"scan output", "nmap.txt", tmp_path / "scratch")
        evidence = stores.evidence_store.add_evidence(
            project_id=project.id, source_path=dest, original_filename="nmap.txt",
            evidence_type="note", max_upload_mb=settings.max_upload_mb,
        )
        assert evidence.id == "EVD-001"
        assert stores.evidence_store.verify_integrity(project.id, evidence.id)
    finally:
        stores.conn.close()
