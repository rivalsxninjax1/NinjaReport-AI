"""Phase 11 acceptance tests: performance for constrained hardware.

Pure stdlib + real Pillow (available in this environment) — no mocking
needed. See scripts/benchmark_pipeline.py for the actual measured
before/after numbers this phase is based on.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.cleanup import clean_scratch_dir
from core.db import get_connection, init_schema
from core.evidence_store import EvidenceStore
from processors.derived_store import DerivedStore
from processors.pipeline import process_evidence
from ui.common import resolve_display_image


@pytest.fixture
def env(tmp_path):
    conn = get_connection(tmp_path / "db" / "test.sqlite3")
    init_schema(conn)
    evidence_store = EvidenceStore(conn, evidence_root=tmp_path / "evidence")
    derived_store = DerivedStore(conn)
    project = evidence_store.create_project("Perf Test")
    yield {
        "conn": conn, "evidence_store": evidence_store, "derived_store": derived_store,
        "project": project, "tmp_path": tmp_path,
    }
    conn.close()


def _make_image_evidence(env, name="shot.png", size=(600, 400)):
    pytest.importorskip("PIL")
    from PIL import Image

    src = env["tmp_path"] / name
    Image.new("RGB", size, "white").save(src)
    return env["evidence_store"].add_evidence(
        project_id=env["project"].id, source_path=src, original_filename=name,
        evidence_type="screenshot", max_upload_mb=50,
    )


# ---------- Idempotent processing (avoid duplicate OCR) ----------

def test_process_evidence_runs_and_creates_artifacts(env):
    evidence = _make_image_evidence(env)
    ran = process_evidence(evidence, derived_root=env["tmp_path"] / "derived", derived_store=env["derived_store"])
    assert ran is True
    artifacts = env["derived_store"].list_artifacts(env["project"].id, evidence.id)
    assert len(artifacts) == 2  # preview + ocr_text


def test_process_evidence_skips_when_already_processed(env):
    evidence = _make_image_evidence(env)
    derived_root = env["tmp_path"] / "derived"
    process_evidence(evidence, derived_root=derived_root, derived_store=env["derived_store"])

    ran_again = process_evidence(evidence, derived_root=derived_root, derived_store=env["derived_store"])
    assert ran_again is False
    artifacts = env["derived_store"].list_artifacts(env["project"].id, evidence.id)
    assert len(artifacts) == 2  # unchanged — no duplicate rows


def test_process_evidence_force_reprocesses_without_duplicating(env):
    evidence = _make_image_evidence(env)
    derived_root = env["tmp_path"] / "derived"
    process_evidence(evidence, derived_root=derived_root, derived_store=env["derived_store"])

    ran = process_evidence(evidence, derived_root=derived_root, derived_store=env["derived_store"], force=True)
    assert ran is True
    artifacts = env["derived_store"].list_artifacts(env["project"].id, evidence.id)
    assert len(artifacts) == 2  # replaced, not doubled to 4


# ---------- Scratch cleanup ----------

def test_clean_scratch_dir_removes_only_scratch_contents(tmp_path):
    scratch = tmp_path / "uploads_scratch"
    scratch.mkdir()
    (scratch / "orphan1.tmp").write_bytes(b"x")
    (scratch / "orphan2.tmp").write_bytes(b"y")
    (scratch / "subdir").mkdir()
    (scratch / "subdir" / "nested.tmp").write_bytes(b"z")

    other_dir = tmp_path / "evidence"
    other_dir.mkdir()
    (other_dir / "keep_me.png").write_bytes(b"real evidence")

    removed = clean_scratch_dir(scratch)
    assert removed == 3
    assert list(scratch.iterdir()) == []
    assert (other_dir / "keep_me.png").exists()


def test_clean_scratch_dir_safe_when_empty_or_missing(tmp_path):
    scratch = tmp_path / "uploads_scratch"
    scratch.mkdir()
    assert clean_scratch_dir(scratch) == 0
    assert clean_scratch_dir(tmp_path / "does_not_exist") == 0


# ---------- Preview-preferred display (avoid loading full-res originals) ----------

def test_resolve_display_image_falls_back_to_original_before_processing(env):
    evidence = _make_image_evidence(env, size=(2000, 1500))
    resolved = resolve_display_image(evidence, env["derived_store"])
    assert resolved == Path(evidence.stored_path)


def test_resolve_display_image_prefers_smaller_preview_after_processing(env):
    pytest.importorskip("PIL")
    from PIL import Image

    evidence = _make_image_evidence(env, size=(2000, 1500))
    process_evidence(evidence, derived_root=env["tmp_path"] / "derived", derived_store=env["derived_store"])

    resolved = resolve_display_image(evidence, env["derived_store"])
    assert resolved != Path(evidence.stored_path)
    assert resolved.exists()
    with Image.open(resolved) as img:
        assert max(img.size) <= 1600
