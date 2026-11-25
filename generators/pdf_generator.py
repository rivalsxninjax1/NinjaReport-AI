"""PDF report generation from a CompiledReport, using reportlab Platypus.

Image-stretch guarantee: reportlab's Image flowable needs both width and
height, so we always compute both via generators.image_fit.compute_display_size()
from the image's real pixel dimensions — never a hardcoded or guessed size.

Formatting: sections are auto-numbered ("1. Executive Summary"), findings
show their traceable ID in the heading ("FIND-001: ..."), and a Table of
Contents page lists the numbered sections right after the cover page.
Note: this TOC is a static list (section titles only) — it is not linked
to page numbers. A page-number-linked TOC is possible in reportlab via a
two-pass build with its TableOfContents flowable, but adds meaningfully
more complexity/risk than this static version for the same core benefit
(a reader can see the report's structure at a glance).

Robustness: reportlab's Paragraph interprets <tags> as markup, so every
piece of dynamic text is cleaned (clean_text) and XML-escaped (_e) first —
otherwise text like "<Press Enter>" would silently vanish from the report.
Only real images get figure numbers; plain-text evidence is shown inline.
"""
from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.platypus import (
    Image as RLImage,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)

from generators.image_fit import DEFAULT_DPI, compute_display_size
from generators.report_compiler import CompiledReport, clean_text, has_renderable_image

MAX_IMAGE_WIDTH_IN = 6.0

_styles = getSampleStyleSheet()
_TITLE = ParagraphStyle("ReportTitle", parent=_styles["Title"], fontSize=28)
_SUBTITLE = ParagraphStyle("ReportSubtitle", parent=_styles["Normal"], fontSize=16, alignment=1)
_GENERATED = ParagraphStyle("Generated", parent=_styles["Normal"], fontSize=10, alignment=1, italic=True)
_H1 = _styles["Heading1"]
_H2 = _styles["Heading2"]
_H3 = _styles["Heading3"]
_BODY = _styles["BodyText"]
_CAPTION = ParagraphStyle("Caption", parent=_styles["Normal"], fontSize=9, alignment=1, italic=True)
_TOC_ENTRY = ParagraphStyle("TocEntry", parent=_styles["Normal"], fontSize=12, leftIndent=12, spaceAfter=6)
_MONO = ParagraphStyle("Mono", parent=_styles["BodyText"], fontName="Courier", fontSize=8.5, leading=11)


def _e(text: str | None) -> str:
    """Clean and XML-escape text so Paragraph shows it literally."""
    return escape(clean_text(text))


class _NumberedCanvas(pdf_canvas.Canvas):
    """Buffers pages so the footer can show 'Page X of Y' — reportlab needs
    a two-pass approach since total page count isn't known while the first
    page is being drawn."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self._draw_footer(total_pages)
            super().showPage()
        super().save()

    def _draw_footer(self, total_pages: int) -> None:
        self.setFont("Helvetica", 9)
        text = f"Page {self._pageNumber} of {total_pages}"
        self.drawCentredString(LETTER[0] / 2, 0.4 * inch, text)


def generate_pdf(report: CompiledReport, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(output_path), pagesize=LETTER,
        topMargin=0.75 * inch, bottomMargin=0.75 * inch,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
    )

    story = _build_cover(report)
    story.extend(_build_table_of_contents(report))
    figure_number = 0

    for section_number, section in enumerate(report.sections, start=1):
        story.append(Paragraph(f"{section_number}. {_e(section.title)}", _H1))

        for item in section.items:
            if item.item_type == "text":
                if item.caption:
                    story.append(Paragraph(_e(item.caption), _H3))
                for paragraph_text in clean_text(item.text).split("\n"):
                    if paragraph_text.strip():
                        story.append(Paragraph(escape(paragraph_text), _BODY))

            elif item.item_type == "evidence":
                if has_renderable_image(item):
                    figure_number += 1
                story.extend(_build_evidence_item(item, figure_number))

            elif item.item_type == "finding":
                story.extend(_build_finding_item(item))

    doc.build(story, canvasmaker=_NumberedCanvas)
    return output_path


def _build_cover(report: CompiledReport) -> list:
    return [
        Spacer(1, 2 * inch),
        Paragraph(_e(report.project_name), _TITLE),
        Spacer(1, 0.25 * inch),
        Paragraph(f"{report.template_type.upper()} Report", _SUBTITLE),
        Spacer(1, 0.5 * inch),
        Paragraph(f"Generated: {report.generated_at}", _GENERATED),
        PageBreak(),
    ]


def _build_table_of_contents(report: CompiledReport) -> list:
    flowables = [Paragraph("Table of Contents", _H1), Spacer(1, 0.15 * inch)]
    for section_number, section in enumerate(report.sections, start=1):
        flowables.append(Paragraph(f"{section_number}. {_e(section.title)}", _TOC_ENTRY))
    flowables.append(PageBreak())
    return flowables


def _build_evidence_item(item, figure_number: int) -> list:
    if not has_renderable_image(item):
        return _build_non_image_evidence(item)

    width_px, height_px = item.image_size_px or (0, 0)
    display = compute_display_size(width_px, height_px, MAX_IMAGE_WIDTH_IN, dpi=DEFAULT_DPI)

    image = RLImage(
        str(item.image_path),
        width=display.width_in * inch,
        height=display.height_in * inch,
    )
    caption = Paragraph(f"Figure {figure_number}: {_e(item.caption)}", _CAPTION)
    return [image, Spacer(1, 0.1 * inch), caption, Spacer(1, 0.2 * inch)]


def _build_non_image_evidence(item) -> list:
    """Plain-text evidence is shown inline; anything else is referenced by
    name. No figure number — only real images get those."""
    evidence_id = item.evidence.id if item.evidence else ""
    filename = _e(item.evidence.original_filename) if item.evidence else ""
    caption = _e(item.caption)
    heading = f"{evidence_id}: {caption}" if evidence_id else caption
    flowables = [Paragraph(f"<b>{heading}</b>", _BODY)]

    if item.image_path is not None:  # the record points at an image whose file is gone
        flowables.append(Paragraph(f"[Image file not available: {filename}]", _BODY))
    elif item.text_excerpt:
        flowables.append(Paragraph(_e(item.text_excerpt).replace("\n", "<br/>"), _MONO))
    else:
        flowables.append(Paragraph(
            f"File: {filename} (not an image — referenced here, kept in the project's evidence folder)", _BODY,
        ))
    flowables.append(Spacer(1, 0.2 * inch))
    return flowables


def _build_finding_item(item) -> list:
    finding = item.finding
    flowables = [Paragraph(f"{finding.id}: {_e(finding.title)}", _H2)]

    severity_text = finding.severity if finding.severity else "Not yet approved by reviewer"
    flowables.append(Paragraph(f"<b>Severity:</b> {severity_text}", _BODY))
    flowables.append(Paragraph(f"<b>Status:</b> {finding.status}", _BODY))
    flowables.append(Paragraph(f"<b>Affected Asset:</b> {_e(finding.affected_asset) or 'Not specified'}", _BODY))

    if finding.description:
        flowables.append(Paragraph("Description", _H3))
        flowables.append(Paragraph(_e(finding.description), _BODY))

    if finding.technical_impact:
        flowables.append(Paragraph("Technical Impact", _H3))
        flowables.append(Paragraph(_e(finding.technical_impact), _BODY))

    if finding.business_impact:
        flowables.append(Paragraph("Business Impact", _H3))
        flowables.append(Paragraph(_e(finding.business_impact), _BODY))

    if finding.reproduction_steps:
        flowables.append(Paragraph("Reproduction Steps", _H3))
        flowables.append(
            ListFlowable(
                [ListItem(Paragraph(_e(step), _BODY)) for step in finding.reproduction_steps],
                bulletType="1",
            )
        )

    if finding.remediation:
        flowables.append(Paragraph("Remediation", _H3))
        flowables.append(Paragraph(_e(finding.remediation), _BODY))

    cvss_text = _e(finding.cvss_vector) if finding.cvss_vector else "Not provided"
    flowables.append(Paragraph(f"<i>CVSS Vector: {cvss_text}</i>", _BODY))

    if finding.references_list:
        flowables.append(Paragraph("References", _H3))
        flowables.append(
            ListFlowable(
                [ListItem(Paragraph(_e(ref), _BODY)) for ref in finding.references_list],
                bulletType="bullet",
            )
        )

    if finding.evidence_ids:
        flowables.append(Paragraph(f"Referenced evidence: {', '.join(finding.evidence_ids)}", _BODY))

    flowables.append(Spacer(1, 0.2 * inch))
    return flowables
