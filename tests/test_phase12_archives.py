"""Phase 12 acceptance tests: project archives.

Pure stdlib (zipfile, sqlite3, shutil) + real Pillow — fully executable.
Covers the two riskiest scenarios directly: numeric-ID collision on import
into a database that already has other projects, and hash-based tamper
detection on a corrupted/modified archive.
"""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import pytest

from config import Settings
from core.archive import ArchiveError, ArchiveImportError, export_project, import_project, read_manifest
from core.db import get_connection, init_schema
from core.db_backup import backup_database, list_backups, restore_database
from core.evidence_store import EvidenceStore
from core.findings_store import FindingsStore
from core.project_lifecycle import safe_delete_project
from core.report_store import ReportStore


def _make_settings(base_dir: Path) -> Settings:
    settings = Settings(data_dir=base_dir / "appdata", export_dir=base_dir / "appdata" / "exports")
    settings.ensure_directories()
    return settings


@pytest.fixture
def live_env(tmp_path):
    """A 'live' app instance that already has one project (B) in it,
    occupying the low end of every autoincrement sequence — exactly the
    scenario where naive ID copying would collide."""
    pytest.importorskip("PIL")
    from PIL import Image

    settings = _make_settings(tmp_path / "main")
    conn = get_connection(settings.db_path())
    init_schema(conn)
    evidence_store = EvidenceStore(conn, evidence_root=settings.data_dir / "evidence")
    findings_store = FindingsStore(conn)
    report_store = ReportStore(conn)

    project_b = evidence_store.create_project("Project B (pre-existing)")
    src_b = tmp_path / "b.png"
    Image.new("RGB", (100, 100), "blue").save(src_b)
    ev_b = evidence_store.add_evidence(
        project_id=project_b.id, source_path=src_b, original_filename="b.png",
        evidence_type="screenshot", max_upload_mb=50,
    )
    plan_b = report_store.create_plan(project_b.id, "ctf")
    report_store.add_evidence_item(project_b.id, plan_b.sections[0].id, ev_b.id, evidence_store=evidence_store)
    report_store.add_text_item(plan_b.sections[0].id, "B's text")

    yield {
        "conn": conn, "settings": settings, "evidence_store": evidence_store,
        "findings_store": findings_store, "report_store": report_store, "project_b": project_b,
    }
    conn.close()


@pytest.fixture
def archive_a(tmp_path):
    """Build and export project A from a completely separate app instance,
    returning the finished .nra path (which outlives this fixture's own
    temp resources)."""
    pytest.importorskip("PIL")
    from PIL import Image

    src_settings = _make_settings(tmp_path / "source_a")
    conn = get_connection(src_settings.db_path())
    init_schema(conn)
    evidence_store = EvidenceStore(conn, evidence_root=src_settings.data_dir / "evidence")
    findings_store = FindingsStore(conn)
    report_store = ReportStore(conn)

    project_a = evidence_store.create_project("Project A (to import)")
    src_a = tmp_path / "a.png"
    Image.new("RGB", (200, 150), "red").save(src_a)
    ev_a = evidence_store.add_evidence(
        project_id=project_a.id, source_path=src_a, original_filename="a.png",
        evidence_type="screenshot", max_upload_mb=50,
    )
    finding_a = findings_store.create_finding(
        project_a.id, title="A's finding", evidence_ids=[ev_a.id], evidence_store=evidence_store,
    )
    plan_a = report_store.create_plan(project_a.id, "vapt")
    section_a = plan_a.sections[0].id
    report_store.add_evidence_item(project_a.id, section_a, ev_a.id, caption="A's evidence caption", evidence_store=evidence_store)
    report_store.add_finding_item(project_a.id, section_a, finding_a.id, findings_store=findings_store)
    report_store.add_text_item(section_a, "A's text item")

    archive_path = tmp_path / "project_a.nra"
    export_project(project_a.id, conn, src_settings, archive_path)
    conn.close()

    return {"path": archive_path, "project_id": project_a.id, "evidence_id": ev_a.id, "finding_id": finding_a.id}


# ---------- Export ----------

def test_export_creates_archive_with_manifest(archive_a):
    assert archive_a["path"].exists()
    manifest = read_manifest(archive_a["path"])
    assert manifest.project_id == archive_a["project_id"]
    assert manifest.format_version == "1"
    assert len(manifest.file_hashes) > 0


# ---------- Import: the critical multi-project ID-collision scenario ----------

def test_import_alongside_existing_project_does_not_collide(live_env, archive_a):
    imported_id = import_project(archive_a["path"], live_env["conn"], live_env["settings"])
    assert imported_id == archive_a["project_id"]


def test_existing_project_untouched_after_import(live_env, archive_a):
    import_project(archive_a["path"], live_env["conn"], live_env["settings"])

    b_plan = live_env["report_store"].get_plan(live_env["project_b"].id)
    assert b_plan.sections[0].items[0].item_type == "evidence"
    assert b_plan.sections[0].items[1].text_content == "B's text"


def test_imported_report_hierarchy_correctly_remapped(live_env, archive_a):
    import_project(archive_a["path"], live_env["conn"], live_env["settings"])

    plan = live_env["report_store"].get_plan(archive_a["project_id"])
    assert plan.template_type == "vapt"
    items = plan.sections[0].items
    assert any(i.item_type == "evidence" and i.caption == "A's evidence caption" for i in items)
    assert any(i.item_type == "finding" for i in items)
    assert any(i.item_type == "text" and i.text_content == "A's text item" for i in items)


def test_imported_evidence_hash_verifies(live_env, archive_a):
    import_project(archive_a["path"], live_env["conn"], live_env["settings"])
    evidence_store = live_env["evidence_store"]
    assert evidence_store.verify_integrity(archive_a["project_id"], archive_a["evidence_id"])


def test_imported_finding_present(live_env, archive_a):
    import_project(archive_a["path"], live_env["conn"], live_env["settings"])
    findings = live_env["findings_store"].list_findings(archive_a["project_id"])
    assert len(findings) == 1
    assert findings[0].title == "A's finding"


def test_reimporting_same_project_id_is_rejected(live_env, archive_a):
    import_project(archive_a["path"], live_env["conn"], live_env["settings"])
    with pytest.raises(ArchiveImportError, match="already exists"):
        import_project(archive_a["path"], live_env["conn"], live_env["settings"])


# ---------- Tamper / corruption detection ----------

def test_import_rejects_tampered_evidence_file(live_env, archive_a, tmp_path):
    tampered_path = tmp_path / "tampered.nra"
    shutil.copy2(archive_a["path"], tampered_path)

    with zipfile.ZipFile(tampered_path, "r") as zin:
        contents = {n: zin.read(n) for n in zin.namelist()}
    evidence_entry = next(n for n in contents if n.startswith(f"evidence/{archive_a['project_id']}/"))
    contents[evidence_entry] = b"TAMPERED BYTES REPLACING ORIGINAL EVIDENCE"
    with zipfile.ZipFile(tampered_path, "w") as zout:
        for name, data in contents.items():
            zout.writestr(name, data)

    with pytest.raises(ArchiveImportError, match="[Hh]ash"):
        import_project(tampered_path, live_env["conn"], live_env["settings"])

    # And the project must NOT have been partially created.
    assert live_env["evidence_store"].get_project(archive_a["project_id"]) is None


def test_import_rejects_completely_corrupted_archive(live_env, tmp_path):
    fake = tmp_path / "garbage.nra"
    fake.write_bytes(b"not a zip file at all")
    with pytest.raises(ArchiveImportError):
        import_project(fake, live_env["conn"], live_env["settings"])


def test_import_rejects_unsupported_format_version(live_env, archive_a, tmp_path):
    modified_path = tmp_path / "future_version.nra"
    shutil.copy2(archive_a["path"], modified_path)

    with zipfile.ZipFile(modified_path, "r") as zin:
        contents = {n: zin.read(n) for n in zin.namelist()}
    import json
    manifest = json.loads(contents["manifest.json"])
    manifest["format_version"] = "999"
    contents["manifest.json"] = json.dumps(manifest).encode()
    with zipfile.ZipFile(modified_path, "w") as zout:
        for name, data in contents.items():
            zout.writestr(name, data)

    with pytest.raises(ArchiveImportError, match="version"):
        import_project(modified_path, live_env["conn"], live_env["settings"])


# ---------- Backup / restore ----------

def test_backup_and_list(tmp_path):
    settings = _make_settings(tmp_path)
    conn = get_connection(settings.db_path())
    init_schema(conn)
    EvidenceStore(conn, evidence_root=settings.data_dir / "evidence").create_project("Backup Test")
    conn.close()

    backup_dir = tmp_path / "backups"
    backup_path = backup_database(settings.db_path(), backup_dir)
    assert backup_path.exists()
    assert list_backups(backup_dir) == [backup_path]


def test_restore_recovers_lost_database(tmp_path):
    settings = _make_settings(tmp_path)
    conn = get_connection(settings.db_path())
    init_schema(conn)
    EvidenceStore(conn, evidence_root=settings.data_dir / "evidence").create_project("Recoverable")
    conn.close()

    backup_path = backup_database(settings.db_path(), tmp_path / "backups")
    settings.db_path().unlink()

    preserved = restore_database(backup_path, settings.db_path())
    assert preserved is None  # nothing existed to preserve

    conn2 = get_connection(settings.db_path())
    rows = conn2.execute("SELECT name FROM projects").fetchall()
    assert rows[0]["name"] == "Recoverable"
    conn2.close()


def test_restore_preserves_existing_db_rather_than_deleting_it(tmp_path):
    settings = _make_settings(tmp_path)
    conn = get_connection(settings.db_path())
    init_schema(conn)
    conn.close()
    backup_path = backup_database(settings.db_path(), tmp_path / "backups")

    conn2 = get_connection(settings.db_path())
    EvidenceStore(conn2, evidence_root=settings.data_dir / "evidence").create_project("Newer, not yet backed up")
    conn2.close()

    preserved = restore_database(backup_path, settings.db_path())
    assert preserved is not None
    assert preserved.exists()


# ---------- Safe deletion (export-first gate) ----------

def test_safe_delete_refuses_without_export(tmp_path):
    settings = _make_settings(tmp_path)
    conn = get_connection(settings.db_path())
    init_schema(conn)
    evidence_store = EvidenceStore(conn, evidence_root=settings.data_dir / "evidence")
    project = evidence_store.create_project("To Delete")

    with pytest.raises(ArchiveError, match="Refusing to delete"):
        safe_delete_project(conn, settings, project.id)
    assert evidence_store.get_project(project.id) is not None
    conn.close()


def test_safe_delete_succeeds_with_matching_export(tmp_path):
    settings = _make_settings(tmp_path)
    conn = get_connection(settings.db_path())
    init_schema(conn)
    evidence_store = EvidenceStore(conn, evidence_root=settings.data_dir / "evidence")
    project = evidence_store.create_project("To Delete")

    export_path = tmp_path / "export.nra"
    export_project(project.id, conn, settings, export_path)
    safe_delete_project(conn, settings, project.id, export_path=export_path)
    assert evidence_store.get_project(project.id) is None
    conn.close()


def test_safe_delete_refuses_mismatched_export(tmp_path):
    settings = _make_settings(tmp_path)
    conn = get_connection(settings.db_path())
    init_schema(conn)
    evidence_store = EvidenceStore(conn, evidence_root=settings.data_dir / "evidence")
    project_1 = evidence_store.create_project("Project 1")
    project_2 = evidence_store.create_project("Project 2")

    export_path = tmp_path / "export.nra"
    export_project(project_1.id, conn, settings, export_path)

    with pytest.raises(ArchiveError):
        safe_delete_project(conn, settings, project_2.id, export_path=export_path)
    assert evidence_store.get_project(project_2.id) is not None
    conn.close()


def test_safe_delete_allows_explicit_skip(tmp_path):
    settings = _make_settings(tmp_path)
    conn = get_connection(settings.db_path())
    init_schema(conn)
    evidence_store = EvidenceStore(conn, evidence_root=settings.data_dir / "evidence")
    project = evidence_store.create_project("Skip Export")

    safe_delete_project(conn, settings, project.id, allow_skip_export=True)
    assert evidence_store.get_project(project.id) is None
    conn.close()
