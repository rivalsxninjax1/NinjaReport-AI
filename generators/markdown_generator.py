"""Markdown report generation from a CompiledReport.

Simplest of the three generators — no page layout or image-stretch
concerns (a Markdown viewer scales images itself), but the content
structure mirrors the DOCX/PDF generators: numbered sections, finding IDs
in headings, a Table of Contents (here a real anchor-linked list, since
Markdown viewers/GitHub auto-generate heading anchors), and the same
"not yet approved by reviewer" honesty for unapproved severities.

All text goes through clean_text() first (ANSI codes / control characters
from pasted terminal output), and plain-text evidence is shown inline in a
fenced code block rather than just being named.
"""
from __future__ import annotations

import re
from pathlib import Path

from generators.report_compiler import CompiledReport, clean_text, has_renderable_image


def _slugify(heading_text: str) -> str:
    """Approximate the anchor-slug most Markdown renderers (GitHub, VS
    Code preview) generate from a heading, so Table of Contents links
    actually land on the right section."""
    slug = heading_text.lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"\s+", "-", slug.strip())
    return slug


def _one_line(text: str | None) -> str:
    """Headings and image alt-text must stay on a single line."""
    return " ".join(clean_text(text).split())


def _fenced(text: str) -> list[str]:
    """Wrap text in a code fence that is guaranteed longer than any run of
    backticks inside it, so pasted content can't break out of the block."""
    fence = "```"
    while fence in text:
        fence += "`"
    return [fence, text, fence, ""]


def generate_markdown(report: CompiledReport, output_path: Path) -> Path:
    lines: list[str] = []
    lines.append(f"# {_one_line(report.project_name)}")
    lines.append(f"## {report.template_type.upper()} Report")
    lines.append(f"*Generated: {report.generated_at}*")
    lines.append("")

    section_headings = [f"{i}. {_one_line(s.title)}" for i, s in enumerate(report.sections, start=1)]
    if section_headings:
        lines.append("## Table of Contents")
        for heading in section_headings:
            lines.append(f"- [{heading}](#{_slugify(heading)})")
        lines.append("")

    figure_number = 0
    for section_number, section in enumerate(report.sections, start=1):
        lines.append(f"## {section_number}. {_one_line(section.title)}")
        lines.append("")

        for item in section.items:
            if item.item_type == "text":
                if item.caption:
                    lines.append(f"### {_one_line(item.caption)}")
                for paragraph in clean_text(item.text).split("\n"):
                    if paragraph.strip():
                        lines.append(paragraph)
                        lines.append("")

            elif item.item_type == "evidence":
                if has_renderable_image(item):
                    figure_number += 1
                lines.extend(_render_evidence_item(item, figure_number))

            elif item.item_type == "finding":
                lines.extend(_render_finding_item(item))

        lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


def _render_evidence_item(item, figure_number: int) -> list[str]:
    caption = _one_line(item.caption)
    if has_renderable_image(item):
        alt = caption.replace("[", "(").replace("]", ")")
        return [f"![{alt}]({item.image_path})", f"*Figure {figure_number}: {caption}*", ""]

    evidence_id = item.evidence.id if item.evidence else ""
    filename = _one_line(item.evidence.original_filename) if item.evidence else ""
    heading = f"{evidence_id}: {caption}" if evidence_id else caption
    lines = [f"**{heading}**", ""]
    if item.image_path is not None:
        lines += [f"*[Image file not available: {filename}]*", ""]
    elif item.text_excerpt:
        lines += _fenced(clean_text(item.text_excerpt))
    else:
        lines += [f"*File: {filename} (not an image — referenced here, kept in the project's evidence folder)*", ""]
    return lines


def _render_finding_item(item) -> list[str]:
    finding = item.finding
    c = clean_text
    lines = [f"### {finding.id}: {_one_line(finding.title)}", ""]

    severity_text = finding.severity if finding.severity else "Not yet approved by reviewer"
    lines.append(f"**Severity:** {severity_text}  ")
    lines.append(f"**Status:** {finding.status}  ")
    lines.append(f"**Affected Asset:** {_one_line(finding.affected_asset) or 'Not specified'}")
    lines.append("")

    if finding.description:
        lines.append("**Description**")
        lines.append(c(finding.description))
        lines.append("")

    if finding.technical_impact:
        lines.append("**Technical Impact**")
        lines.append(c(finding.technical_impact))
        lines.append("")

    if finding.business_impact:
        lines.append("**Business Impact**")
        lines.append(c(finding.business_impact))
        lines.append("")

    if finding.reproduction_steps:
        lines.append("**Reproduction Steps**")
        for i, step in enumerate(finding.reproduction_steps, start=1):
            lines.append(f"{i}. {_one_line(step)}")
        lines.append("")

    if finding.remediation:
        lines.append("**Remediation**")
        lines.append(c(finding.remediation))
        lines.append("")

    cvss_text = _one_line(finding.cvss_vector) if finding.cvss_vector else "Not provided"
    lines.append(f"*CVSS Vector: {cvss_text}*")
    lines.append("")

    if finding.references_list:
        lines.append("**References**")
        for ref in finding.references_list:
            lines.append(f"- {_one_line(ref)}")
        lines.append("")

    if finding.evidence_ids:
        lines.append(f"Referenced evidence: {', '.join(finding.evidence_ids)}")
        lines.append("")

    return lines
