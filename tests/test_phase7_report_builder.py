"""Phase 7 acceptance tests: report builder.

Pure stdlib + SQLite — fully executable in any environment.
"""
from __future__ import annotations

import pytest

from core.db import get_connection, init_schema
from core.evidence_store import EvidenceStore
from core.findings_store import FindingsStore
from core.report_store import ReportStore
from core.report_templates import CTF_TEMPLATE_SECTIONS, VAPT_TEMPLATE_SECTIONS


@pytest.fixture
def env(tmp_path):
    conn = get_connection(tmp_path / "db" / "test.sqlite3")
    init_schema(conn)
    evidence_store = EvidenceStore(conn, evidence_root=tmp_path / "evidence")
    findings_store = FindingsStore(conn)
    report_store = ReportStore(conn)

    project = evidence_store.create_project("Phase 7 Test")
    src = tmp_path / "scan.txt"
    src.write_bytes(b"nmap output")
    evidence = evidence_store.add_evidence(
        project_id=project.id, source_path=src, original_filename="scan.txt",
        evidence_type="note", max_upload_mb=50,
    )
    finding = findings_store.create_finding(project.id, title="Open SSH port")

    yield {
        "conn": conn, "evidence_store": evidence_store, "findings_store": findings_store,
        "report_store": report_store, "project": project, "evidence": evidence, "finding": finding,
    }
    conn.close()


# ---------- Template bootstrap ----------

def test_create_plan_bootstraps_vapt_sections(env):
    report_store: ReportStore = env["report_store"]
    plan = report_store.create_plan(env["project"].id, "vapt")
    assert plan.template_type == "vapt"
    assert [s.title for s in plan.sections] == VAPT_TEMPLATE_SECTIONS
    assert all(s.included for s in plan.sections)
    assert all(s.items == [] for s in plan.sections)


def test_create_plan_bootstraps_ctf_sections(env):
    report_store: ReportStore = env["report_store"]
    plan = report_store.create_plan(env["project"].id, "ctf")
    assert [s.title for s in plan.sections] == CTF_TEMPLATE_SECTIONS


def test_create_plan_rejects_unknown_template(env):
    report_store: ReportStore = env["report_store"]
    with pytest.raises(ValueError):
        report_store.create_plan(env["project"].id, "not_a_real_template")


# ---------- One plan per project ----------

def test_creating_second_plan_raises(env):
    report_store: ReportStore = env["report_store"]
    project_id = env["project"].id
    report_store.create_plan(project_id, "vapt")
    with pytest.raises(ValueError):
        report_store.create_plan(project_id, "ctf")


def test_delete_plan_allows_recreation(env):
    report_store: ReportStore = env["report_store"]
    project_id = env["project"].id
    report_store.create_plan(project_id, "vapt")
    report_store.delete_plan(project_id)
    assert report_store.get_plan(project_id) is None

    new_plan = report_store.create_plan(project_id, "ctf")
    assert new_plan.template_type == "ctf"


def test_get_plan_returns_none_when_no_plan_exists(env):
    report_store: ReportStore = env["report_store"]
    assert report_store.get_plan(env["project"].id) is None


# ---------- Section management ----------

def test_add_custom_section_appends_at_end(env):
    report_store: ReportStore = env["report_store"]
    project_id = env["project"].id
    report_store.create_plan(project_id, "ctf")

    section = report_store.add_section(project_id, "Custom Appendix B")
    plan = report_store.get_plan(project_id)
    assert plan.sections[-1].id == section.id
    assert plan.sections[-1].title == "Custom Appendix B"
    assert len(plan.sections) == len(CTF_TEMPLATE_SECTIONS) + 1


def test_reorder_sections_changes_order(env):
    report_store: ReportStore = env["report_store"]
    project_id = env["project"].id
    plan = report_store.create_plan(project_id, "ctf")

    ids = [s.id for s in plan.sections]
    reversed_ids = list(reversed(ids))
    report_store.reorder_sections(project_id, reversed_ids)

    reordered = report_store.get_plan(project_id)
    assert [s.id for s in reordered.sections] == reversed_ids
    assert [s.title for s in reordered.sections] == list(reversed(CTF_TEMPLATE_SECTIONS))


def test_reorder_sections_rejects_mismatched_id_set(env):
    report_store: ReportStore = env["report_store"]
    project_id = env["project"].id
    plan = report_store.create_plan(project_id, "ctf")
    ids = [s.id for s in plan.sections][:-1]  # drop one — invalid
    with pytest.raises(ValueError):
        report_store.reorder_sections(project_id, ids)


def test_set_section_included_toggles(env):
    report_store: ReportStore = env["report_store"]
    project_id = env["project"].id
    plan = report_store.create_plan(project_id, "ctf")
    section = plan.sections[0]

    report_store.set_section_included(section.id, False)
    updated = report_store.get_plan(project_id)
    assert updated.sections[0].included is False


# ---------- Item placement ----------

def test_add_evidence_item_validated_against_evidence_store(env):
    report_store: ReportStore = env["report_store"]
    project_id = env["project"].id
    evidence = env["evidence"]
    plan = report_store.create_plan(project_id, "ctf")
    section = plan.sections[0]

    item = report_store.add_evidence_item(
        project_id, section.id, evidence.id, caption="Nmap scan showing port 22",
        evidence_store=env["evidence_store"],
    )
    assert item.item_type == "evidence"
    assert item.evidence_id == evidence.id
    assert item.caption == "Nmap scan showing port 22"


def test_add_evidence_item_rejects_unknown_evidence(env):
    report_store: ReportStore = env["report_store"]
    project_id = env["project"].id
    plan = report_store.create_plan(project_id, "ctf")
    with pytest.raises(ValueError):
        report_store.add_evidence_item(
            project_id, plan.sections[0].id, "EVD-999", evidence_store=env["evidence_store"],
        )


def test_add_finding_item_validated_against_findings_store(env):
    report_store: ReportStore = env["report_store"]
    project_id = env["project"].id
    finding = env["finding"]
    plan = report_store.create_plan(project_id, "ctf")

    item = report_store.add_finding_item(
        project_id, plan.sections[0].id, finding.id, findings_store=env["findings_store"],
    )
    assert item.item_type == "finding"
    assert item.finding_id == finding.id


def test_add_finding_item_rejects_unknown_finding(env):
    report_store: ReportStore = env["report_store"]
    project_id = env["project"].id
    plan = report_store.create_plan(project_id, "ctf")
    with pytest.raises(ValueError):
        report_store.add_finding_item(
            project_id, plan.sections[0].id, "FIND-999", findings_store=env["findings_store"],
        )


def test_add_text_item(env):
    report_store: ReportStore = env["report_store"]
    project_id = env["project"].id
    plan = report_store.create_plan(project_id, "ctf")
    item = report_store.add_text_item(plan.sections[0].id, "Manual narrative paragraph.")
    assert item.item_type == "text"
    assert item.text_content == "Manual narrative paragraph."


def test_items_ordered_by_insertion_within_section(env):
    report_store: ReportStore = env["report_store"]
    project_id = env["project"].id
    plan = report_store.create_plan(project_id, "ctf")
    section_id = plan.sections[0].id

    report_store.add_text_item(section_id, "first")
    report_store.add_text_item(section_id, "second")
    report_store.add_text_item(section_id, "third")

    updated = report_store.get_plan(project_id)
    section = next(s for s in updated.sections if s.id == section_id)
    assert [i.text_content for i in section.items] == ["first", "second", "third"]


def test_reorder_items_changes_order(env):
    report_store: ReportStore = env["report_store"]
    project_id = env["project"].id
    plan = report_store.create_plan(project_id, "ctf")
    section_id = plan.sections[0].id

    i1 = report_store.add_text_item(section_id, "first")
    i2 = report_store.add_text_item(section_id, "second")

    report_store.reorder_items(section_id, [i2.id, i1.id])
    updated = report_store.get_plan(project_id)
    section = next(s for s in updated.sections if s.id == section_id)
    assert [i.text_content for i in section.items] == ["second", "first"]


def test_set_item_included_and_caption(env):
    report_store: ReportStore = env["report_store"]
    project_id = env["project"].id
    plan = report_store.create_plan(project_id, "ctf")
    item = report_store.add_text_item(plan.sections[0].id, "some text")

    report_store.set_item_included(item.id, False)
    report_store.set_item_caption(item.id, "Updated caption")

    updated_item = report_store.get_item(item.id)
    assert updated_item.included is False
    assert updated_item.caption == "Updated caption"


def test_add_item_to_unknown_section_still_persists_gracefully(env):
    # No FK enforcement bug surfaced here — item insertion doesn't validate
    # section existence beyond the FK constraint itself; this documents that
    # a bogus section_id raises via SQLite's foreign key enforcement.
    report_store: ReportStore = env["report_store"]
    with pytest.raises(Exception):
        report_store.add_text_item(999999, "orphan text")
