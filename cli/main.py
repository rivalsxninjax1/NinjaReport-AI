"""NinjaReport AI command-line interface.

Deliberately minimal — the Streamlit UI is primary. These commands exist
for quick diagnostics and scripted/headless use, and reuse the exact same
stores and functions the UI calls (ui.common.build_stores, analyze_evidence,
compile_report, export_project) rather than duplicating any logic.

Usage:
    python -m cli doctor
    python -m cli projects
    python -m cli analyze /path/to/screenshot.png [--project PRJ-xxxx]
    python -m cli generate PRJ-xxxx
    python -m cli export PRJ-xxxx [output.nra]
"""
from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ninjareport", description="NinjaReport AI command-line tools")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="Check Ollama/OCR/PDF availability and data paths")
    sub.add_parser("projects", help="List all projects")

    p_analyze = sub.add_parser("analyze", help="Ingest (and, if AI is available, analyze) a single evidence file")
    p_analyze.add_argument("path", type=Path)
    p_analyze.add_argument("--project", dest="project_id", default=None, help="Existing project ID (default: auto-create/reuse a 'CLI Imports' project)")

    p_generate = sub.add_parser("generate", help="Generate DOCX+PDF for a project's report plan")
    p_generate.add_argument("project_id")

    p_export = sub.add_parser("export", help="Export a project as a portable .nra archive")
    p_export.add_argument("project_id")
    p_export.add_argument("output", nargs="?", type=Path, default=None)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    from ui.common import build_stores
    stores = build_stores()
    try:
        if args.command == "doctor":
            return cmd_doctor(stores)
        if args.command == "projects":
            return cmd_projects(stores)
        if args.command == "analyze":
            return cmd_analyze(stores, args.path, args.project_id)
        if args.command == "generate":
            return cmd_generate(stores, args.project_id)
        if args.command == "export":
            return cmd_export(stores, args.project_id, args.output)
        return 1
    finally:
        stores.conn.close()


def cmd_doctor(stores) -> int:
    from ai.diagnostics import format_diagnostics, run_diagnostics
    from core.system_diagnostics import check_system_dependencies, format_system_diagnostics

    print(f"Data directory:   {stores.settings.data_dir}")
    print(f"Export directory: {stores.settings.export_dir}")
    print(f"Database:         {stores.settings.db_path()}")
    print()

    diagnostics = run_diagnostics(
        stores.ollama, stores.settings.ollama_text_model, stores.settings.ollama_vision_model,
    )
    print(format_diagnostics(diagnostics))
    print()
    print(format_system_diagnostics(check_system_dependencies()))
    return 0


def cmd_projects(stores) -> int:
    projects = stores.evidence_store.list_projects()
    if not projects:
        print("No projects yet.")
        return 0
    for project in projects:
        evidence_count = len(stores.evidence_store.list_evidence(project.id))
        findings_count = len(stores.findings_store.list_findings(project.id))
        print(f"{project.id}  {project.name!r}  evidence={evidence_count}  findings={findings_count}")
    return 0


def cmd_analyze(stores, path: Path, project_id: str | None) -> int:
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        return 1

    if project_id:
        project = stores.evidence_store.get_project(project_id)
        if project is None:
            print(f"Unknown project: {project_id}", file=sys.stderr)
            return 1
    else:
        project = next((p for p in stores.evidence_store.list_projects() if p.name == "CLI Imports"), None)
        if project is None:
            project = stores.evidence_store.create_project("CLI Imports")
        print(f"Using project {project.id} ({project.name!r})")

    suffix = path.suffix.lower()
    evidence_type = "screenshot" if suffix in (".png", ".jpg", ".jpeg") else "pdf" if suffix == ".pdf" else "note"

    from processors.pipeline import process_evidence
    try:
        evidence = stores.evidence_store.add_evidence(
            project_id=project.id, source_path=path, original_filename=path.name,
            evidence_type=evidence_type, max_upload_mb=stores.settings.max_upload_mb,
        )
    except ValueError as exc:
        print(f"Could not ingest file: {exc}", file=sys.stderr)
        return 1

    process_evidence(evidence, derived_root=stores.settings.data_dir / "derived", derived_store=stores.derived_store)
    print(f"Ingested as {evidence.id} ({evidence.mime_type}, {evidence.size_bytes} bytes)")

    if not stores.settings.ollama_text_model:
        print("No OLLAMA_TEXT_MODEL configured — skipping AI analysis.")
        return 0

    from ai.evidence_analyzer import analyze_evidence
    from ai.provider import AIProviderError, OfflineModeError
    try:
        analysis = analyze_evidence(
            evidence, stores.derived_store, stores.ollama, stores.ai_cache, model=stores.settings.ollama_text_model,
        )
    except OfflineModeError as exc:
        print(f"Ollama offline — skipping AI analysis: {exc}")
        return 0
    except AIProviderError as exc:
        print(f"AI analysis failed: {exc}", file=sys.stderr)
        return 0

    print(f"Classification: {analysis.classification}")
    print(f"Summary: {analysis.summary}")
    if analysis.uncertainties:
        print("Uncertainties:")
        for u in analysis.uncertainties:
            print(f"  - {u}")
    print(f"Verification status: {analysis.verification_status} (review in the UI to change this)")
    return 0


def cmd_generate(stores, project_id: str) -> int:
    project = stores.evidence_store.get_project(project_id)
    if project is None:
        print(f"Unknown project: {project_id}", file=sys.stderr)
        return 1

    plan = stores.report_store.get_plan(project_id)
    if plan is None:
        print(f"No report plan exists for {project_id} yet — build one in the UI first.", file=sys.stderr)
        return 1

    from generators.docx_generator import generate_docx
    from generators.pdf_generator import generate_pdf
    from generators.report_compiler import ReportCompilationError, compile_report

    try:
        compiled = compile_report(project.name, plan, stores.evidence_store, stores.findings_store)
    except ReportCompilationError as exc:
        print(f"Cannot generate report: {exc}", file=sys.stderr)
        return 1

    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    export_dir = stores.settings.export_dir / project_id
    docx_path = generate_docx(compiled, export_dir / f"report_{timestamp}.docx")
    pdf_path = generate_pdf(compiled, export_dir / f"report_{timestamp}.pdf")
    print(f"Generated: {docx_path}")
    print(f"Generated: {pdf_path}")
    return 0


def cmd_export(stores, project_id: str, output: Path | None) -> int:
    if stores.evidence_store.get_project(project_id) is None:
        print(f"Unknown project: {project_id}", file=sys.stderr)
        return 1

    from core.archive import ArchiveError, export_project
    output = output or Path.cwd() / f"{project_id}.nra"
    try:
        export_project(project_id, stores.conn, stores.settings, output)
    except ArchiveError as exc:
        print(f"Export failed: {exc}", file=sys.stderr)
        return 1
    print(f"Exported: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
