"""Markdown report generation from a CompiledReport.

Simplest of the three generators — no page layout or image-stretch
concerns (a Markdown viewer scales images itself), but the content
structure mirrors the DOCX/PDF generators exactly: same cover info, same
figure numbering, same finding fields, same "not yet approved by
reviewer" honesty for unapproved severities.
"""
from __future__ import annotations

from pathlib import Path

from generators.report_compiler import CompiledReport


def generate_markdown(report: CompiledReport, output_path: Path) -> Path:
    lines: list[str] = []
    lines.append(f"# {report.project_name}")
    lines.append(f"### {report.template_type.upper()} Report")
    lines.append(f"*Generated: {report.generated_at}*")
    lines.append("")

    figure_number = 0
    for section in report.sections:
        lines.append(f"## {section.title}")
        lines.append("")

        for item in section.items:
            if item.item_type == "text":
                if item.caption:
                    lines.append(f"### {item.caption}")
                for paragraph in (item.text or "").split("\n"):
                    if paragraph.strip():
                        lines.append(paragraph)
                        lines.append("")

            elif item.item_type == "evidence":
                figure_number += 1
                lines.extend(_render_evidence_item(item, figure_number))

            elif item.item_type == "finding":
                lines.extend(_render_finding_item(item))

        lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


def _render_evidence_item(item, figure_number: int) -> list[str]:
    if item.image_path is None or not item.image_path.exists():
        return [f"*[Evidence file not available: {item.caption}]*", ""]

    return [
        f"![{item.caption}]({item.image_path})",
        f"*Figure {figure_number}: {item.caption}*",
        "",
    ]


def _render_finding_item(item) -> list[str]:
    finding = item.finding
    lines = [f"### {finding.title}", ""]

    severity_text = finding.severity if finding.severity else "Not yet approved by reviewer"
    lines.append(f"**Severity:** {severity_text}  ")
    lines.append(f"**Status:** {finding.status}  ")
    lines.append(f"**Affected Asset:** {finding.affected_asset or 'Not specified'}")
    lines.append("")

    if finding.description:
        lines.append("**Description**")
        lines.append(finding.description)
        lines.append("")

    if finding.technical_impact:
        lines.append("**Technical Impact**")
        lines.append(finding.technical_impact)
        lines.append("")

    if finding.business_impact:
        lines.append("**Business Impact**")
        lines.append(finding.business_impact)
        lines.append("")

    if finding.reproduction_steps:
        lines.append("**Reproduction Steps**")
        for i, step in enumerate(finding.reproduction_steps, start=1):
            lines.append(f"{i}. {step}")
        lines.append("")

    if finding.remediation:
        lines.append("**Remediation**")
        lines.append(finding.remediation)
        lines.append("")

    cvss_text = finding.cvss_vector if finding.cvss_vector else "Not provided"
    lines.append(f"*CVSS Vector: {cvss_text}*")
    lines.append("")

    if finding.references_list:
        lines.append("**References**")
        for ref in finding.references_list:
            lines.append(f"- {ref}")
        lines.append("")

    if finding.evidence_ids:
        lines.append(f"Referenced evidence: {', '.join(finding.evidence_ids)}")
        lines.append("")

    return lines
