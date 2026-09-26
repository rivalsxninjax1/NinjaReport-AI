"""Quick Report: the one-shot 'dump everything, get a report' pipeline.

Ties together pieces that already existed independently — ingestion
(Phase 1/2), per-evidence AI analysis (Phase 4), findings (Phase 6), the
report builder (Phase 7), and the generators (Phase 8) — behind a single
entry point. Every AI step degrades gracefully: if Ollama is offline or a
step fails, the report still generates with whatever succeeded, and never
silently fabricates the parts that didn't.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ai.evidence_analyzer import analyze_evidence
from ai.provider import AIProviderError, OfflineModeError
from ai.report_synthesizer import synthesize_findings, synthesize_narrative
from ai.schemas import EvidenceAnalysis
from core.models import Evidence, Finding
from core.report_store import ReportPlan
from core.safe_files import infer_evidence_type
from generators.docx_generator import generate_docx
from generators.markdown_generator import generate_markdown
from generators.pdf_generator import generate_pdf
from generators.report_compiler import compile_report

# Which of each template's own sections get AI-written narrative prose.
NARRATIVE_SECTIONS = {
    "ctf": ["Introduction & Objective", "Methodology", "Attack Path / Walkthrough", "Conclusion"],
    "vapt": ["Executive Summary", "Scope", "Methodology", "Attack Narrative", "Conclusion"],
}
FINDINGS_SECTION_TITLES = {"Findings Summary", "Detailed Findings"}
EVIDENCE_APPENDIX_TITLES = {"Evidence Appendix"}
# Sections that need human input (reviewer names, sign-off) or that the
# generators already render separately (the cover page) — excluded by
# default in the auto-built plan rather than left as empty headings.
NO_AUTO_CONTENT_TITLES = {"Cover", "Document Control"}

GENERATORS = {"docx": generate_docx, "pdf": generate_pdf, "md": generate_markdown}
EXTENSIONS = {"docx": ".docx", "pdf": ".pdf", "md": ".md"}


@dataclass
class QuickReportResult:
    project_id: str
    project_name: str
    evidence: list[Evidence] = field(default_factory=list)
    analyses: list[tuple[Evidence, EvidenceAnalysis]] = field(default_factory=list)
    analysis_errors: list[tuple[Evidence, str]] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    findings_error: str | None = None
    narrative_error: str | None = None
    plan: ReportPlan | None = None
    output_path: Path | None = None


def ingest_inputs(
    stores,
    project_id: str,
    pasted_text: str | None,
    uploaded_files: list[tuple[bytes, str]],
) -> list[Evidence]:
    """Save pasted text and/or uploaded files as evidence, then run
    OCR/preview processing on each. Returns the created Evidence records
    in the order they were ingested."""
    from processors.pipeline import process_evidence
    from ui.common import save_uploaded_bytes

    scratch_dir = stores.settings.data_dir / "uploads_scratch"
    derived_root = stores.settings.data_dir / "derived"
    created: list[Evidence] = []

    if pasted_text and pasted_text.strip():
        path = save_uploaded_bytes(pasted_text.encode("utf-8"), "pasted_notes.txt", scratch_dir)
        try:
            evidence = stores.evidence_store.add_evidence(
                project_id=project_id, source_path=path, original_filename="pasted_notes.txt",
                evidence_type="note", max_upload_mb=stores.settings.max_upload_mb,
            )
            process_evidence(evidence, derived_root=derived_root, derived_store=stores.derived_store)
            created.append(evidence)
        finally:
            path.unlink(missing_ok=True)

    for data, filename in uploaded_files:
        path = save_uploaded_bytes(data, filename, scratch_dir)
        try:
            evidence = stores.evidence_store.add_evidence(
                project_id=project_id, source_path=path, original_filename=filename,
                evidence_type=infer_evidence_type(filename), max_upload_mb=stores.settings.max_upload_mb,
            )
            process_evidence(evidence, derived_root=derived_root, derived_store=stores.derived_store)
            created.append(evidence)
        except ValueError:
            pass  # skip files that fail validation (disallowed type, too large, duplicate); don't abort the batch
        finally:
            path.unlink(missing_ok=True)

    return created


def analyze_batch(
    stores, evidence_list: list[Evidence], model: str,
) -> tuple[list[tuple[Evidence, EvidenceAnalysis]], list[tuple[Evidence, str]]]:
    """Analyze each evidence item independently — one item's failure
    doesn't abort the rest of the batch."""
    results: list[tuple[Evidence, EvidenceAnalysis]] = []
    errors: list[tuple[Evidence, str]] = []
    for evidence in evidence_list:
        try:
            analysis = analyze_evidence(
                evidence, stores.derived_store, stores.ollama, stores.ai_cache, model=model,
            )
            results.append((evidence, analysis))
        except (OfflineModeError, AIProviderError) as exc:
            errors.append((evidence, str(exc)))
    return results, errors


def build_report_plan_automatically(
    stores,
    project_id: str,
    template_type: str,
    evidence_list: list[Evidence],
    analyses: list[tuple[Evidence, EvidenceAnalysis]],
    findings: list[Finding],
    narrative_texts: dict[str, str],
) -> ReportPlan:
    report_store = stores.report_store
    plan = report_store.create_plan(project_id, template_type)
    analyzed_ids = {evidence.id for evidence, _ in analyses}

    for section in plan.sections:
        title = section.title

        if title in NO_AUTO_CONTENT_TITLES:
            report_store.set_section_included(section.id, False)

        elif title in narrative_texts:
            report_store.add_text_item(section.id, narrative_texts[title])

        elif title in FINDINGS_SECTION_TITLES:
            if findings:
                for finding in findings:
                    report_store.add_finding_item(
                        project_id, section.id, finding.id, findings_store=stores.findings_store,
                    )
            else:
                report_store.set_section_included(section.id, False)

        elif title in EVIDENCE_APPENDIX_TITLES:
            if evidence_list:
                for evidence in evidence_list:
                    analysis = next((a for e, a in analyses if e.id == evidence.id), None)
                    caption = analysis.suggested_caption if analysis and analysis.suggested_caption else evidence.original_filename
                    report_store.add_evidence_item(
                        project_id, section.id, evidence.id, caption=caption, evidence_store=stores.evidence_store,
                    )
            else:
                report_store.set_section_included(section.id, False)

        elif title == "Tools":
            tools = sorted({t for _, a in analyses for t in a.tools})
            if tools:
                report_store.add_text_item(section.id, "\n".join(f"- {t}" for t in tools))
            else:
                report_store.set_section_included(section.id, False)

        elif title == "Flags Captured":
            flags = sorted({f for _, a in analyses for f in a.flags})
            if flags:
                report_store.add_text_item(section.id, "\n".join(f"- {f}" for f in flags))
            else:
                report_store.set_section_included(section.id, False)

        else:
            # A section this function has no auto-content strategy for
            # (or the AI never got run, so analyzed_ids is empty): leave it
            # in the plan, empty, for manual editing later rather than
            # guessing at content.
            if not analyzed_ids:
                report_store.set_section_included(section.id, False)

    return report_store.get_plan(project_id)


def generate_output(stores, project_id: str, output_format: str) -> Path:
    if output_format not in GENERATORS:
        raise ValueError(f"Unknown output format: {output_format}. Must be one of {list(GENERATORS)}")

    plan = stores.report_store.get_plan(project_id)
    if plan is None:
        raise ValueError(f"No report plan exists for project {project_id}")

    project = stores.evidence_store.get_project(project_id)
    compiled = compile_report(project.name, plan, stores.evidence_store, stores.findings_store)

    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    export_dir = stores.settings.export_dir / project_id
    output_path = export_dir / f"quick_report_{timestamp}{EXTENSIONS[output_format]}"
    return GENERATORS[output_format](compiled, output_path)


def run_quick_report(
    stores,
    pasted_text: str | None,
    uploaded_files: list[tuple[bytes, str]],
    template_type: str,
    output_format: str,
    project_id: str | None = None,
    project_name: str | None = None,
) -> QuickReportResult:
    """The full one-shot pipeline. Always returns a result object — even
    partial failures (Ollama offline, one bad file) still produce the best
    report possible from what succeeded, never an unhandled exception for
    an AI-side problem."""
    if project_id:
        project = stores.evidence_store.get_project(project_id)
        if project is None:
            raise ValueError(f"Unknown project: {project_id}")
    else:
        name = project_name or f"Quick Report {datetime.now(UTC).strftime('%Y-%m-%d %H:%M')}"
        project = stores.evidence_store.create_project(name)

    result = QuickReportResult(project_id=project.id, project_name=project.name)

    result.evidence = ingest_inputs(stores, project.id, pasted_text, uploaded_files)

    model = stores.settings.ollama_text_model
    if model and result.evidence:
        health = stores.ollama.health_check()
        if health.healthy:
            result.analyses, result.analysis_errors = analyze_batch(stores, result.evidence, model)
        else:
            result.analysis_errors = [(e, f"Ollama offline: {health.reason}") for e in result.evidence]
    elif result.evidence:
        result.analysis_errors = [(e, "No OLLAMA_TEXT_MODEL configured") for e in result.evidence]

    if result.analyses:
        try:
            result.findings = synthesize_findings(
                result.analyses, stores.ollama, model, stores.findings_store, stores.evidence_store, project.id,
            )
        except AIProviderError as exc:
            result.findings_error = str(exc)

        try:
            narrative_texts = synthesize_narrative(
                result.analyses, result.findings, NARRATIVE_SECTIONS.get(template_type, []), stores.ollama, model,
            )
        except AIProviderError as exc:
            result.narrative_error = str(exc)
            narrative_texts = {}
    else:
        narrative_texts = {}

    result.plan = build_report_plan_automatically(
        stores, project.id, template_type, result.evidence, result.analyses, result.findings, narrative_texts,
    )
    result.output_path = generate_output(stores, project.id, output_format)

    return result
