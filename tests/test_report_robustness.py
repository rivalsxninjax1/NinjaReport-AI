"""Report-robustness tests: hostile pasted text must never crash or corrupt a report.

Pasted terminal output carries ANSI colour codes, angle brackets, ampersands
and backtick fences. These tests generate real DOCX/PDF/Markdown files from
such content, and also cover figure numbering and non-image evidence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from core.models import Evidence, Finding
from generators.docx_generator import generate_docx
from generators.markdown_generator import generate_markdown
from generators.pdf_generator import generate_pdf
from generators.report_compiler import clean_text, compile_report


@dataclass
class _Item:
    item_type: str
    included: bool = True
    evidence_id: str | None = None
    finding_id: str | None = None
    text_content: str | None = None
    caption: str = ""


@dataclass
class _Section:
    title: str
    items: list = field(default_factory=list)
    included: bool = True


@dataclass
class _Plan:
    project_id: str
    template_type: str
    sections: list


class _EvidenceStore:
    def __init__(self, items):
        self.items = items

    def get_evidence(self, project_id, evidence_id):
        return self.items.get(evidence_id)

    def verify_integrity(self, project_id, evidence_id):
        return True


class _FindingsStore:
    def __init__(self, items):
        self.items = items

    def get_finding(self, project_id, finding_id):
        return self.items.get(finding_id)


def _evidence(evidence_id, filename, path, mime):
    return Evidence(evidence_id, "P", filename, filename, str(path), "note", mime, 0, "hash", "now")


def _pdf_text(path: Path) -> str:
    """Extract text with whichever PDF library is installed."""
    for module_name in ("pymupdf", "fitz"):
        try:
            module = __import__(module_name)
        except ImportError:
            continue
        with module.open(str(path)) as doc:
            return "\n".join(page.get_text() for page in doc)
    try:
        import pypdf
    except ImportError:
        pytest.skip("no PDF text extractor installed")
    return "\n".join(page.extract_text() for page in pypdf.PdfReader(str(path)).pages)


@pytest.fixture
def compiled(tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image

    Image.new("RGB", (800, 400), "white").save(tmp_path / "shot.png")
    note = tmp_path / "pasted_notes.txt"
    note.write_text(
        "\x1b[32m$ nmap -sV 10.0.0.5\x1b[0m\r\n22/tcp open ssh\r\n"
        "Press <Press Enter> & continue\nfence test ``` inside\n",
        encoding="utf-8",
    )
    (tmp_path / "scan.pdf").write_bytes(b"%PDF-1.4 fake")

    evidence = {
        "EVD-001": _evidence("EVD-001", "pasted_notes.txt", note, "text/plain"),
        "EVD-002": _evidence("EVD-002", "shot.png", tmp_path / "shot.png", "image/png"),
        "EVD-003": _evidence("EVD-003", "scan.pdf", tmp_path / "scan.pdf", "application/pdf"),
        "EVD-004": _evidence("EVD-004", "gone.png", tmp_path / "gone.png", "image/png"),  # file missing
    }
    finding = Finding(
        "FIND-001", "P", "\x1b[31mRed\x1b[0m title <x>", "now", "now",
        description="Use <b>bold</b> & <script>alert(1)</script>\x0c here",
        affected_asset="10.0.0.5", reproduction_steps=["Run <cmd> & see"],
    )
    plan = _Plan("P", "ctf", [
        _Section("Narrative", [_Item("text", text_content="\x1b[1mBold\x1b[0m intro\r\nsecond <line>", caption="Intro <1>")]),
        _Section("Findings Summary", [_Item("finding", finding_id="FIND-001")]),
        _Section("Evidence Appendix", [_Item("evidence", evidence_id=e, caption=f"cap {e}") for e in evidence]),
    ])
    return compile_report(
        "Proj \x1b[0m<X>", plan, _EvidenceStore(evidence), _FindingsStore({"FIND-001": finding}),
    )


def test_clean_text_strips_ansi_and_control_chars():
    assert clean_text("\x1b[32mgreen\x1b[0m output") == "green output"
    assert clean_text("null\x00byte") == "nullbyte"
    assert clean_text("form\x0cfeed") == "formfeed"
    assert clean_text("tab\tand\r\nwindows\rline") == "tab\tand\nwindows\nline"
    assert clean_text("red & <tag>") == "red & <tag>"
    assert clean_text(None) == ""


def test_text_excerpt_only_for_plain_text_evidence(compiled):
    by_id = {i.evidence.id: i for i in compiled.sections[2].items}
    excerpt = by_id["EVD-001"].text_excerpt
    assert excerpt and "\x1b" not in excerpt and "\r" not in excerpt
    assert "<Press Enter>" in excerpt
    assert by_id["EVD-002"].text_excerpt is None
    assert by_id["EVD-003"].text_excerpt is None


def test_docx_survives_hostile_text_and_numbers_only_real_images(compiled, tmp_path):
    pytest.importorskip("docx")
    from docx import Document

    document = Document(str(generate_docx(compiled, tmp_path / "r.docx")))
    text = "\n".join(p.text for p in document.paragraphs)

    assert "\x1b" not in text and "\x0c" not in text
    assert "22/tcp open ssh" in text and "<Press Enter> & continue" in text
    assert "Figure 1: cap EVD-002" in text and "Figure 2" not in text
    assert len(document.inline_shapes) == 1
    assert "[Image file not available: gone.png]" in text
    assert "File: scan.pdf (not an image" in text
    assert "Not yet approved by reviewer" in text
    shape = document.inline_shapes[0]
    assert abs(shape.width / shape.height - 2.0) < 0.02  # aspect ratio still preserved


def test_pdf_shows_markup_like_text_literally(compiled, tmp_path):
    pytest.importorskip("reportlab")
    text = " ".join(_pdf_text(generate_pdf(compiled, tmp_path / "r.pdf")).split())

    assert "<Press Enter>" in text
    assert "Use <b>bold</b> & <script>alert(1)</script>" in text
    assert "Figure 1: cap EVD-002" in text and "Figure 2" not in text
    assert "[Image file not available: gone.png]" in text
    assert "\x1b" not in text


def test_markdown_is_sanitized_and_fence_cannot_be_broken(compiled, tmp_path):
    markdown = generate_markdown(compiled, tmp_path / "r.md").read_text()

    assert "\x1b" not in markdown and "\r" not in markdown
    assert "````" in markdown  # fence is longer than the ``` inside the pasted text
    assert "Figure 1: cap EVD-002" in markdown and "Figure 2" not in markdown
    assert "![cap EVD-002](" in markdown
    assert "*[Image file not available: gone.png]*" in markdown
    assert "### FIND-001: Red title <x>" in markdown
