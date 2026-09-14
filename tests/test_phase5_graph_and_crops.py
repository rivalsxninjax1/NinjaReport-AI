"""Phase 5 acceptance tests: smart screenshots + evidence graph.

Uses real Pillow (available in this environment) and real SQLite — no
mocking needed for this phase's dependencies.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.crop_store import CropStore
from core.db import get_connection, init_schema
from core.evidence_store import EvidenceStore
from core.graph_store import RelationshipStore
from core.hashing import sha256_file
from core.search import search_evidence
from processors.crop_suggester import apply_crop, suggest_crops
from processors.derived_store import DerivedStore


# ---------- Crop suggestion heuristic ----------

def test_suggest_crops_detects_content_region_on_padded_image(tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (800, 600), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle([300, 250, 500, 350], fill="black")  # small content block, lots of white padding
    path = tmp_path / "padded.png"
    img.save(path)

    suggestions = suggest_crops(path)
    assert len(suggestions) == 2  # tight crop + full-image fallback

    tight = suggestions[0]
    fallback = suggestions[1]
    assert (tight.x2 - tight.x1) < 800
    assert (tight.y2 - tight.y1) < 600
    assert 0.0 < tight.confidence <= 1.0
    assert fallback.x1 == 0 and fallback.y1 == 0
    assert fallback.x2 == 800 and fallback.y2 == 600


def test_suggest_crops_returns_only_fallback_for_uniform_image(tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image

    img = Image.new("RGB", (400, 300), "blue")
    path = tmp_path / "uniform.png"
    img.save(path)

    suggestions = suggest_crops(path)
    assert len(suggestions) == 1
    only = suggestions[0]
    assert (only.x1, only.y1, only.x2, only.y2) == (0, 0, 400, 300)
    assert only.confidence == 0.9  # high confidence: nothing better to suggest


def test_apply_crop_never_modifies_original(tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image

    src = tmp_path / "source.png"
    Image.new("RGB", (500, 400), "red").save(src)
    original_hash = sha256_file(src)

    dest = tmp_path / "crop.jpg"
    apply_crop(src, (50, 50, 200, 200), dest)

    assert dest.exists()
    with Image.open(dest) as cropped:
        assert cropped.size == (150, 150)
    assert sha256_file(src) == original_hash


# ---------- Crop suggestion + decision workflow (DB-backed) ----------

@pytest.fixture
def crop_env(tmp_path):
    conn = get_connection(tmp_path / "db" / "test.sqlite3")
    init_schema(conn)
    evidence_store = EvidenceStore(conn, evidence_root=tmp_path / "evidence")
    crop_store = CropStore(conn)

    project = evidence_store.create_project("Phase 5 Test")

    pytest.importorskip("PIL")
    from PIL import Image

    src = tmp_path / "shot.png"
    Image.new("RGB", (800, 600), "white").save(src)
    evidence = evidence_store.add_evidence(
        project_id=project.id, source_path=src, original_filename="shot.png",
        evidence_type="screenshot", max_upload_mb=50,
    )
    yield {
        "conn": conn, "evidence_store": evidence_store, "crop_store": crop_store,
        "project": project, "evidence": evidence, "tmp_path": tmp_path,
    }
    conn.close()


def test_add_and_list_crop_suggestions(crop_env):
    crop_store: CropStore = crop_env["crop_store"]
    evidence = crop_env["evidence"]

    crop_store.add_suggestion(evidence.project_id, evidence.id, 10, 10, 200, 200, "test region", 0.8)
    suggestions = crop_store.list_suggestions(evidence.project_id, evidence.id)
    assert len(suggestions) == 1
    assert suggestions[0].status == "pending"
    assert suggestions[0].confidence == 0.8


def test_reject_suggestion_leaves_no_derived_file(crop_env):
    crop_store: CropStore = crop_env["crop_store"]
    evidence = crop_env["evidence"]

    suggestion = crop_store.add_suggestion(evidence.project_id, evidence.id, 10, 10, 200, 200, "test", 0.8)
    rejected = crop_store.reject(suggestion.id)
    assert rejected.status == "rejected"
    assert rejected.derived_path is None


def test_accept_suggestion_generates_derived_crop_and_preserves_original(crop_env):
    crop_store: CropStore = crop_env["crop_store"]
    evidence_store: EvidenceStore = crop_env["evidence_store"]
    evidence = crop_env["evidence"]
    tmp_path = crop_env["tmp_path"]

    suggestion = crop_store.add_suggestion(evidence.project_id, evidence.id, 10, 10, 200, 200, "test", 0.8)
    accepted = crop_store.accept(
        suggestion.id,
        source_path=Path(evidence.stored_path),
        derived_root=tmp_path / "derived",
    )
    assert accepted.status == "accepted"
    assert accepted.derived_path is not None
    assert Path(accepted.derived_path).exists()
    # Original evidence remains untouched and verifiable.
    assert evidence_store.verify_integrity(evidence.project_id, evidence.id) is True


def test_adjust_suggestion_uses_new_coordinates_and_marks_adjusted(crop_env):
    crop_store: CropStore = crop_env["crop_store"]
    evidence = crop_env["evidence"]
    tmp_path = crop_env["tmp_path"]

    suggestion = crop_store.add_suggestion(evidence.project_id, evidence.id, 10, 10, 200, 200, "test", 0.8)
    adjusted = crop_store.accept(
        suggestion.id,
        source_path=Path(evidence.stored_path),
        derived_root=tmp_path / "derived",
        coords=(0, 0, 400, 300),
    )
    assert adjusted.status == "adjusted"
    assert (adjusted.x1, adjusted.y1, adjusted.x2, adjusted.y2) == (0, 0, 400, 300)


# ---------- Relationship graph ----------

@pytest.fixture
def graph(tmp_path):
    conn = get_connection(tmp_path / "db" / "graph.sqlite3")
    init_schema(conn)
    yield RelationshipStore(conn)
    conn.close()


def test_add_and_list_relationship_from(graph):
    graph.add_relationship("PRJ-1", "evidence", "EVD-001", "host", "10.0.0.5", "observed_in")
    rels = graph.list_from("PRJ-1", "evidence", "EVD-001")
    assert len(rels) == 1
    assert rels[0].to_type == "host"
    assert rels[0].to_id == "10.0.0.5"
    assert rels[0].relation_type == "observed_in"


def test_list_relationship_to(graph):
    graph.add_relationship("PRJ-1", "evidence", "EVD-001", "host", "10.0.0.5", "observed_in")
    graph.add_relationship("PRJ-1", "evidence", "EVD-002", "host", "10.0.0.5", "observed_in")
    rels = graph.list_to("PRJ-1", "host", "10.0.0.5")
    assert len(rels) == 2
    assert {r.from_id for r in rels} == {"EVD-001", "EVD-002"}


def test_duplicate_relationship_is_idempotent(graph):
    graph.add_relationship("PRJ-1", "evidence", "EVD-001", "tool", "nmap", "uses_tool")
    graph.add_relationship("PRJ-1", "evidence", "EVD-001", "tool", "nmap", "uses_tool")  # duplicate
    rels = graph.list_from("PRJ-1", "evidence", "EVD-001")
    assert len(rels) == 1


def test_remove_relationship(graph):
    graph.add_relationship("PRJ-1", "evidence", "EVD-001", "tool", "nmap", "uses_tool")
    graph.remove_relationship("PRJ-1", "evidence", "EVD-001", "tool", "nmap", "uses_tool")
    assert graph.list_from("PRJ-1", "evidence", "EVD-001") == []


def test_relationships_scoped_by_project(graph):
    graph.add_relationship("PRJ-1", "evidence", "EVD-001", "tool", "nmap", "uses_tool")
    graph.add_relationship("PRJ-2", "evidence", "EVD-001", "tool", "nmap", "uses_tool")
    assert len(graph.list_from("PRJ-1", "evidence", "EVD-001")) == 1
    assert len(graph.list_from("PRJ-2", "evidence", "EVD-001")) == 1


# ---------- Search + filters ----------

@pytest.fixture
def search_env(tmp_path):
    conn = get_connection(tmp_path / "db" / "search.sqlite3")
    init_schema(conn)
    evidence_store = EvidenceStore(conn, evidence_root=tmp_path / "evidence")
    derived_store = DerivedStore(conn)
    project = evidence_store.create_project("Search Test")

    src1 = tmp_path / "a.txt"
    src1.write_bytes(b"first file")
    ev1 = evidence_store.add_evidence(
        project_id=project.id, source_path=src1, original_filename="nmap_scan.txt",
        evidence_type="note", max_upload_mb=50, tags=["recon"],
    )
    derived_store.add_artifact(
        project.id, ev1.id, "ocr_text", engine="tesseract",
        text_content="open port 22 ssh detected",
    )

    src2 = tmp_path / "b.txt"
    src2.write_bytes(b"second file")
    ev2 = evidence_store.add_evidence(
        project_id=project.id, source_path=src2, original_filename="creds.txt",
        evidence_type="note", max_upload_mb=50, tags=["credentials"],
    )
    evidence_store.set_verification_status(project.id, ev2.id, "verified")

    yield {
        "evidence_store": evidence_store, "derived_store": derived_store,
        "project": project, "ev1": ev1, "ev2": ev2,
    }
    conn.close()


def test_search_by_query_matches_ocr_text(search_env):
    results = search_evidence(
        search_env["evidence_store"], search_env["derived_store"],
        search_env["project"].id, query="ssh",
    )
    assert [r.id for r in results] == [search_env["ev1"].id]


def test_search_by_query_matches_filename(search_env):
    results = search_evidence(
        search_env["evidence_store"], search_env["derived_store"],
        search_env["project"].id, query="creds",
    )
    assert [r.id for r in results] == [search_env["ev2"].id]


def test_search_filters_by_tags(search_env):
    results = search_evidence(
        search_env["evidence_store"], search_env["derived_store"],
        search_env["project"].id, tags=["recon"],
    )
    assert [r.id for r in results] == [search_env["ev1"].id]


def test_search_filters_by_verification_status(search_env):
    results = search_evidence(
        search_env["evidence_store"], search_env["derived_store"],
        search_env["project"].id, verification_status="verified",
    )
    assert [r.id for r in results] == [search_env["ev2"].id]


def test_search_with_no_filters_returns_all(search_env):
    results = search_evidence(
        search_env["evidence_store"], search_env["derived_store"], search_env["project"].id,
    )
    assert len(results) == 2
