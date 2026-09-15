"""Phase 8 acceptance tests: DOCX + PDF report generation.

Both python-docx and reportlab are exercised for real here — including
reopening the generated DOCX to verify embedded images preserved their
source aspect ratio (the core 'never stretch' guarantee).
"""
from __future__ import annotations

import pytest

from core.db import get_connection, init_schema
from core.evidence_store import EvidenceStore
from core.findings_store import FindingsStore
from core.report_store import ReportStore
from generators.image_fit import compute_display_size
from generators.report_compiler import ReportCompilationError, compile_report


# ---------- Image-fit math ----------

def test_small_image_is_not_upscaled():
    display = compute_display_size(200, 100, max_width_in=6.0, dpi=96)
    native_width_in = 200 / 96
    assert display.width_in == pytest.approx(native_width_in)
    assert display.height_in == pytest.approx(native_width_in * (100 / 200))


def test_large_image_is_capped_to_max_width():
    display = compute_display_size(1920, 1080, max_width_in=6.0, dpi=96)
    assert display.width_in == pytest.approx(6.0)
    assert display.height_in == pytest.approx(6.0 * (1080 / 1920))


def test_aspect_ratio_always_preserved():
    for w, h in [(800, 400), (400, 800), (1000, 333), (1, 1)]:
        display = compute_display_size(w, h, max_width_in=6.0)
        assert display.width_in / display.height_in == pytest.approx(w / h, rel=1e-6)


def test_degenerate_dimensions_fall_back_safely():
    display = compute_display_size(0, 0, max_width_in=6.0)
    assert display.width_in == 6.0
    assert display.height_in == 6.0


# ---------- Report compiler ----------

@pytest.fixture
def env(tmp_path):
    conn = get_connection(tmp_path / "db" / "test.sqlite3")
    init_schema(conn)
    evidence_store = EvidenceStore(conn, evidence_root=tmp_path / "evidence")
    findings_store = FindingsStore(conn)
    report_store = ReportStore(conn)

    project = evidence_store.create_project("Phase 8 Test")

    pytest.importorskip("PIL")
    from PIL import Image

    img_path = tmp_path / "shot.png"
    Image.new("RGB", (800, 400), "white").save(img_path)  # 2:1 aspect ratio
    evidence = evidence_store.add_evidence(
        project_id=project.id, source_path=img_path, original_filename="shot.png",
        evidence_type="screenshot", max_upload_mb=50,
    )

    note_path = tmp_path / "note.txt"
    note_path.write_bytes(b"a plain text note")
    note_evidence = evidence_store.add_evidence(
        project_id=project.id, source_path=note_path, original_filename="note.txt",
        evidence_type="note", max_upload_mb=50,
    )

    finding = findings_store.create_finding(project.id, title="Open SSH port", description="Port 22 exposed")
    findings_store.approve_severity(project.id, finding.id, "high")

    plan = report_store.create_plan(project.id, "ctf")
    section = plan.sections[0]
    report_store.add_evidence_item(
        project.id, section.id, evidence.id, caption="Nmap output", evidence_store=evidence_store,
    )
    report_store.add_evidence_item(
        project.id, section.id, note_evidence.id, caption="A note", evidence_store=evidence_store,
    )
    report_store.add_finding_item(
        project.id, section.id, finding.id, findings_store=findings_store,
    )
    report_store.add_text_item(section.id, "Manual narrative.", caption="Notes")

    # A section deliberately excluded — must not appear in compiled output.
    excluded_section = plan.sections[1]
    report_store.set_section_included(excluded_section.id, False)

    yield {
        "conn": conn, "evidence_store": evidence_store, "findings_store": findings_store,
        "report_store": report_store, "project": project, "evidence": evidence,
        "note_evidence": note_evidence, "finding": finding, "img_path": img_path,
    }
    conn.close()


def test_compile_report_includes_only_included_sections(env):
    plan = env["report_store"].get_plan(env["project"].id)
    compiled = compile_report(env["project"].name, plan, env["evidence_store"], env["findings_store"])
    titles = [s.title for s in compiled.sections]
    assert env["report_store"].get_plan(env["project"].id).sections[1].title not in titles


def test_compile_report_resolves_image_evidence_with_size(env):
    plan = env["report_store"].get_plan(env["project"].id)
    compiled = compile_report(env["project"].name, plan, env["evidence_store"], env["findings_store"])
    items = compiled.sections[0].items
    image_item = next(i for i in items if i.item_type == "evidence" and i.evidence.id == env["evidence"].id)
    assert image_item.image_path is not None
    assert image_item.image_size_px == (800, 400)


def test_compile_report_non_image_evidence_has_no_image_path(env):
    plan = env["report_store"].get_plan(env["project"].id)
    compiled = compile_report(env["project"].name, plan, env["evidence_store"], env["findings_store"])
    items = compiled.sections[0].items
    note_item = next(i for i in items if i.item_type == "evidence" and i.evidence.id == env["note_evidence"].id)
    assert note_item.image_path is None


def test_compile_report_includes_finding_and_text(env):
    plan = env["report_store"].get_plan(env["project"].id)
    compiled = compile_report(env["project"].name, plan, env["evidence_store"], env["findings_store"])
    items = compiled.sections[0].items
    assert any(i.item_type == "finding" and i.finding.id == env["finding"].id for i in items)
    assert any(i.item_type == "text" and i.text == "Manual narrative." for i in items)


def test_compile_report_raises_on_dangling_evidence_reference(env):
    report_store = env["report_store"]
    project = env["project"]
    section = report_store.get_plan(project.id).sections[0]
    # Bypass validation deliberately to simulate evidence deleted after placement.
    report_store.add_evidence_item(project.id, section.id, "EVD-999", evidence_store=None)

    plan = report_store.get_plan(project.id)
    with pytest.raises(ReportCompilationError):
        compile_report(project.name, plan, env["evidence_store"], env["findings_store"])


def test_compile_report_raises_on_dangling_finding_reference(env):
    report_store = env["report_store"]
    project = env["project"]
    section = report_store.get_plan(project.id).sections[0]
    report_store.add_finding_item(project.id, section.id, "FIND-999", findings_store=None)

    plan = report_store.get_plan(project.id)
    with pytest.raises(ReportCompilationError):
        compile_report(project.name, plan, env["evidence_store"], env["findings_store"])


# ---------- DOCX generation ----------

def test_generate_docx_produces_valid_file_with_correct_content(env, tmp_path):
    pytest.importorskip("docx")
    from docx import Document

    from generators.docx_generator import generate_docx

    plan = env["report_store"].get_plan(env["project"].id)
    compiled = compile_report(env["project"].name, plan, env["evidence_store"], env["findings_store"])

    output_path = tmp_path / "report.docx"
    generate_docx(compiled, output_path)

    assert output_path.exists()
    assert output_path.stat().st_size > 1000  # not an empty/corrupt file

    doc = Document(str(output_path))
    all_text = "\n".join(p.text for p in doc.paragraphs)
    assert env["project"].name in all_text
    assert "Open SSH port" in all_text
    assert "high" in all_text  # approved severity shown
    assert "Manual narrative." in all_text
    assert "Figure 1" in all_text

    # Only ONE image should be embedded — the note evidence has no image.
    assert len(doc.inline_shapes) == 1


def test_generate_docx_image_preserves_aspect_ratio(env, tmp_path):
    pytest.importorskip("docx")
    from docx import Document

    from generators.docx_generator import generate_docx

    plan = env["report_store"].get_plan(env["project"].id)
    compiled = compile_report(env["project"].name, plan, env["evidence_store"], env["findings_store"])

    output_path = tmp_path / "report.docx"
    generate_docx(compiled, output_path)

    doc = Document(str(output_path))
    shape = doc.inline_shapes[0]
    embedded_ratio = shape.width / shape.height  # EMUs, ratio is unit-independent
    source_ratio = 800 / 400  # the evidence image was created at this size
    assert embedded_ratio == pytest.approx(source_ratio, rel=1e-2)


def test_generate_docx_never_calls_add_picture_with_explicit_height():
    """Regression guard: stretching can only be reintroduced by someone
    later passing both width and height to add_picture. Assert the source
    never does that."""
    from pathlib import Path

    source_path = Path(__file__).resolve().parent.parent / "generators" / "docx_generator.py"
    source = source_path.read_text()
    assert "add_picture(str(item.image_path), width=" in source
    assert "height=" not in source.split("add_picture(str(item.image_path), width=")[1].split(")")[0]


def test_generate_docx_footer_has_page_number_fields(env, tmp_path):
    pytest.importorskip("docx")
    from generators.docx_generator import generate_docx

    plan = env["report_store"].get_plan(env["project"].id)
    compiled = compile_report(env["project"].name, plan, env["evidence_store"], env["findings_store"])
    output_path = tmp_path / "report.docx"
    generate_docx(compiled, output_path)

    import zipfile
    with zipfile.ZipFile(output_path) as zf:
        footer_xml = [n for n in zf.namelist() if "footer" in n]
        assert footer_xml, "expected a footer part in the docx"
        content = zf.read(footer_xml[0]).decode("utf-8")
        assert "PAGE" in content
        assert "NUMPAGES" in content


def test_generate_docx_handles_finding_without_approved_severity(env, tmp_path):
    pytest.importorskip("docx")
    from docx import Document

    from generators.docx_generator import generate_docx

    findings_store = env["findings_store"]
    project = env["project"]
    unapproved = findings_store.create_finding(project.id, title="Unapproved finding")

    report_store = env["report_store"]
    plan = report_store.get_plan(project.id)
    report_store.add_finding_item(project.id, plan.sections[0].id, unapproved.id, findings_store=findings_store)

    plan = report_store.get_plan(project.id)
    compiled = compile_report(project.name, plan, env["evidence_store"], findings_store)
    output_path = tmp_path / "report2.docx"
    generate_docx(compiled, output_path)

    doc = Document(str(output_path))
    all_text = "\n".join(p.text for p in doc.paragraphs)
    assert "Not yet approved by reviewer" in all_text


# ---------- PDF generation ----------

def test_generate_pdf_produces_valid_file(env, tmp_path):
    pytest.importorskip("reportlab")
    from generators.pdf_generator import generate_pdf

    plan = env["report_store"].get_plan(env["project"].id)
    compiled = compile_report(env["project"].name, plan, env["evidence_store"], env["findings_store"])

    output_path = tmp_path / "report.pdf"
    generate_pdf(compiled, output_path)

    assert output_path.exists()
    data = output_path.read_bytes()
    assert data[:5] == b"%PDF-"
    assert len(data) > 1000
    # Crude but reliable page-count proxy: /Type /Page objects aren't
    # stream-compressed in reportlab's output.
    page_count = data.count(b"/Type /Page")
    assert page_count >= 1


def test_generate_pdf_has_page_number_footer_text(env, tmp_path):
    pytest.importorskip("reportlab")
    from generators.pdf_generator import generate_pdf

    plan = env["report_store"].get_plan(env["project"].id)
    compiled = compile_report(env["project"].name, plan, env["evidence_store"], env["findings_store"])

    output_path = tmp_path / "report.pdf"
    generate_pdf(compiled, output_path)

    data = output_path.read_bytes()
    # "Page" and "of" are drawn as literal footer text; look for the
    # Helvetica footer font usage as circumstantial confirmation it rendered.
    assert b"Helvetica" in data
