"""Compiles a report plan into a generator-neutral structure.

Both the DOCX and PDF generators consume this same CompiledReport, so
section-walking and validation logic lives in exactly one place. Every
evidence/finding reference is validated to still exist before any file
is written — a plan can drift (evidence deleted, etc.) after being built.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from core.evidence_store import EvidenceStore
from core.findings_store import FindingsStore
from core.models import Evidence, Finding
from core.report_store import ReportPlan


class ReportCompilationError(ValueError):
    """Raised when the plan references evidence/findings that no longer exist."""


@dataclass
class CompiledItem:
    item_type: str  # 'evidence' | 'finding' | 'text'
    caption: str = ""
    image_path: Path | None = None
    image_size_px: tuple[int, int] | None = None  # (width, height), None if unknown/not an image
    evidence: Evidence | None = None
    finding: Finding | None = None
    text: str | None = None


@dataclass
class CompiledSection:
    title: str
    items: list[CompiledItem] = field(default_factory=list)


@dataclass
class CompiledReport:
    project_name: str
    template_type: str
    generated_at: str
    sections: list[CompiledSection] = field(default_factory=list)


def compile_report(
    project_name: str,
    plan: ReportPlan,
    evidence_store: EvidenceStore,
    findings_store: FindingsStore,
) -> CompiledReport:
    compiled = CompiledReport(
        project_name=project_name,
        template_type=plan.template_type,
        generated_at=datetime.now(UTC).isoformat(),
    )

    for section in plan.sections:
        if not section.included:
            continue
        compiled_section = CompiledSection(title=section.title)

        for item in section.items:
            if not item.included:
                continue

            if item.item_type == "evidence":
                evidence = evidence_store.get_evidence(plan.project_id, item.evidence_id)
                if evidence is None:
                    raise ReportCompilationError(
                        f"Report plan references evidence {item.evidence_id} which no "
                        f"longer exists in project {plan.project_id}."
                    )
                image_path, image_size = _resolve_image(evidence)
                compiled_section.items.append(
                    CompiledItem(
                        item_type="evidence",
                        caption=item.caption or evidence.description or evidence.original_filename,
                        image_path=image_path,
                        image_size_px=image_size,
                        evidence=evidence,
                    )
                )

            elif item.item_type == "finding":
                finding = findings_store.get_finding(plan.project_id, item.finding_id)
                if finding is None:
                    raise ReportCompilationError(
                        f"Report plan references finding {item.finding_id} which no "
                        f"longer exists in project {plan.project_id}."
                    )
                compiled_section.items.append(
                    CompiledItem(item_type="finding", caption=item.caption, finding=finding)
                )

            elif item.item_type == "text":
                compiled_section.items.append(
                    CompiledItem(item_type="text", caption=item.caption, text=item.text_content or "")
                )

        compiled.sections.append(compiled_section)

    return compiled


def _resolve_image(evidence: Evidence) -> tuple[Path | None, tuple[int, int] | None]:
    """Return (path, (width_px, height_px)) for an evidence item, or
    (None, None) if it isn't an image (e.g. a note or PDF)."""
    mime = evidence.mime_type or ""
    if not mime.startswith("image/"):
        return None, None

    path = Path(evidence.stored_path)
    try:
        from PIL import Image
        with Image.open(path) as img:
            return path, (img.width, img.height)
    except Exception:
        # Image present but unreadable for dimensions — still reference the
        # file; the generator falls back to a safe default size.
        return path, None
