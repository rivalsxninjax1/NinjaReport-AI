"""Quick Report acceptance tests: the one-shot ingest -> analyze ->
synthesize -> build -> generate pipeline.

Non-AI pieces (ingestion, plan building, all three output formats) run
for real. AI-dependent pieces (per-evidence analysis, findings synthesis,
narrative synthesis) use the same FakeTransport pattern as Phases 3/4/10 —
no live Ollama server needed.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from config import Settings
from core.quick_report import (
    build_report_plan_automatically,
    generate_output,
    ingest_inputs,
    run_quick_report,
)
from core.safe_files import infer_evidence_type
from ui.common import build_stores


def _tags_response(models: list[str]) -> dict:
    return {"models": [{"name": m} for m in models]}


class FakeTransport:
    def __init__(self, responses: list):
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict | None]] = []

    def __call__(self, method: str, url: str, payload: dict | None, timeout: float) -> dict:
        self.calls.append((method, url, payload))
        if not self.responses:
            raise AssertionError(f"FakeTransport ran out of scripted responses after {len(self.calls)} calls")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def stores(tmp_path):
    settings = Settings(data_dir=tmp_path / "appdata", export_dir=tmp_path / "appdata" / "exports")
    s = build_stores(settings)
    yield s
    s.conn.close()


@pytest.fixture
def sample_image_bytes(tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image

    path = tmp_path / "shot.png"
    Image.new("RGB", (300, 200), "white").save(path)
    return path.read_bytes()


# ---------- infer_evidence_type ----------

def test_infer_evidence_type_covers_common_cases():
    assert infer_evidence_type("shot.PNG") == "screenshot"
    assert infer_evidence_type("report.pdf") == "pdf"
    assert infer_evidence_type("notes.txt") == "note"
    assert infer_evidence_type("no_extension") == "note"


# ---------- ingest_inputs ----------

def test_ingest_inputs_handles_pasted_text_and_files(stores, sample_image_bytes):
    project = stores.evidence_store.create_project("Ingest Test")
    evidence_list = ingest_inputs(
        stores, project.id,
        pasted_text="nmap scan output: port 22 open ssh",
        uploaded_files=[(sample_image_bytes, "shot.png"), (b"a plain note", "note.txt")],
    )
    assert len(evidence_list) == 3
    assert evidence_list[0].original_filename == "pasted_notes.txt"
    assert evidence_list[0].evidence_type == "note"
    assert evidence_list[1].evidence_type == "screenshot"
    assert evidence_list[2].evidence_type == "note"


def test_ingest_inputs_cleans_up_scratch_files(stores, sample_image_bytes):
    project = stores.evidence_store.create_project("Scratch Test")
    ingest_inputs(stores, project.id, pasted_text="text", uploaded_files=[(sample_image_bytes, "shot.png")])
    scratch = stores.settings.data_dir / "uploads_scratch"
    assert all(not p.is_file() for p in scratch.glob("*"))


def test_ingest_inputs_skips_disallowed_file_without_aborting_batch(stores):
    project = stores.evidence_store.create_project("Mixed Batch")
    evidence_list = ingest_inputs(
        stores, project.id, pasted_text=None,
        uploaded_files=[(b"echo pwned", "malware.sh"), (b"good note", "good.txt")],
    )
    assert len(evidence_list) == 1
    assert evidence_list[0].original_filename == "good.txt"


# ---------- build_report_plan_automatically ----------

def test_plan_without_any_ai_content_excludes_ai_dependent_sections(stores, sample_image_bytes):
    project = stores.evidence_store.create_project("No AI")
    evidence_list = ingest_inputs(stores, project.id, pasted_text="notes", uploaded_files=[(sample_image_bytes, "shot.png")])

    plan = build_report_plan_automatically(
        stores, project.id, "ctf", evidence_list, analyses=[], findings=[], narrative_texts={},
    )
    cover = next(s for s in plan.sections if s.title == "Cover")
    assert cover.included is False

    findings_summary = next(s for s in plan.sections if s.title == "Findings Summary")
    assert findings_summary.included is False

    flags = next(s for s in plan.sections if s.title == "Flags Captured")
    assert flags.included is False

    appendix = next(s for s in plan.sections if s.title == "Evidence Appendix")
    assert appendix.included is True
    assert len(appendix.items) == 2
    assert appendix.items[0].caption == "pasted_notes.txt"


def test_plan_with_ai_content_populates_all_relevant_sections(stores, sample_image_bytes):
    from ai.schemas import EvidenceAnalysis

    project = stores.evidence_store.create_project("Full AI VAPT")
    evidence_list = ingest_inputs(stores, project.id, pasted_text=None, uploaded_files=[(sample_image_bytes, "shot.png")])
    analysis = EvidenceAnalysis(
        evidence_id=evidence_list[0].id, classification="terminal_screenshot",
        summary="Shows an open port.", tools=["nmap"], flags=["flag{test}"],
        suggested_caption="Nmap output showing open port 22",
    )
    finding = stores.findings_store.create_finding(project.id, title="Open port", description="Port 22 open")

    plan = build_report_plan_automatically(
        stores, project.id, "vapt", evidence_list,
        analyses=[(evidence_list[0], analysis)], findings=[finding],
        narrative_texts={"Executive Summary": "This report covers a single open port finding."},
    )

    exec_summary = next(s for s in plan.sections if s.title == "Executive Summary")
    assert exec_summary.items[0].text_content == "This report covers a single open port finding."

    tools_section = next(s for s in plan.sections if s.title == "Tools")
    assert tools_section.included is True
    assert "nmap" in tools_section.items[0].text_content

    findings_section = next(s for s in plan.sections if s.title == "Findings Summary")
    assert findings_section.items[0].item_type == "finding"

    appendix = next(s for s in plan.sections if s.title == "Evidence Appendix")
    assert appendix.items[0].caption == "Nmap output showing open port 22"


def test_flags_captured_populated_in_ctf_template(stores, sample_image_bytes):
    from ai.schemas import EvidenceAnalysis

    project = stores.evidence_store.create_project("CTF Flags")
    evidence_list = ingest_inputs(stores, project.id, pasted_text=None, uploaded_files=[(sample_image_bytes, "shot.png")])
    analysis = EvidenceAnalysis(
        evidence_id=evidence_list[0].id, classification="terminal_screenshot",
        summary="Captured a flag.", flags=["flag{test}"],
    )
    plan = build_report_plan_automatically(
        stores, project.id, "ctf", evidence_list, analyses=[(evidence_list[0], analysis)], findings=[], narrative_texts={},
    )
    flags_section = next(s for s in plan.sections if s.title == "Flags Captured")
    assert flags_section.included is True
    assert "flag{test}" in flags_section.items[0].text_content


# ---------- generate_output ----------

@pytest.mark.parametrize("fmt", ["docx", "pdf", "md"])
def test_generate_output_produces_nonempty_file(stores, sample_image_bytes, fmt):
    project = stores.evidence_store.create_project(f"Format {fmt}")
    evidence_list = ingest_inputs(stores, project.id, pasted_text="notes", uploaded_files=[(sample_image_bytes, "shot.png")])
    build_report_plan_automatically(stores, project.id, "ctf", evidence_list, analyses=[], findings=[], narrative_texts={})

    path = generate_output(stores, project.id, fmt)
    assert path.exists()
    assert path.stat().st_size > 0


def test_generate_output_rejects_unknown_format(stores):
    project = stores.evidence_store.create_project("Bad Format")
    with pytest.raises(ValueError, match="Unknown output format"):
        generate_output(stores, project.id, "docxx")


def test_generate_output_rejects_missing_plan(stores):
    project = stores.evidence_store.create_project("No Plan")
    with pytest.raises(ValueError, match="No report plan"):
        generate_output(stores, project.id, "pdf")


# ---------- AI synthesis (fake transport) ----------

def test_synthesize_findings_creates_finding_and_drops_unknown_evidence_id(stores, sample_image_bytes):
    from ai.ollama_provider import OllamaProvider
    from ai.report_synthesizer import synthesize_findings
    from ai.schemas import EvidenceAnalysis

    project = stores.evidence_store.create_project("Findings Synth")
    evidence_list = ingest_inputs(stores, project.id, pasted_text=None, uploaded_files=[(sample_image_bytes, "shot.png")])
    evidence = evidence_list[0]
    analysis = EvidenceAnalysis(
        evidence_id=evidence.id, classification="terminal_screenshot",
        summary="Open SSH port.", tools=["nmap"], ips=["10.0.0.5"], ports=[22],
    )

    findings_payload = {
        "findings": [{
            "title": "Open SSH port", "description": "Port 22 is open.",
            "affected_asset": "10.0.0.5", "technical_impact": "", "business_impact": "",
            "remediation": "", "suggested_severity": "medium", "severity_rationale": "Exposed service.",
            "evidence_ids": [evidence.id, "EVD-999"], "uncertainties": [],
        }]
    }
    transport = FakeTransport([
        _tags_response(["llama3"]), _tags_response(["llama3"]),
        {"response": json.dumps(findings_payload)},
    ])
    provider = OllamaProvider("http://127.0.0.1:11434", transport=transport)

    findings = synthesize_findings(
        [(evidence, analysis)], provider, "llama3", stores.findings_store, stores.evidence_store, project.id,
    )
    assert len(findings) == 1
    assert findings[0].evidence_ids == [evidence.id]
    assert findings[0].severity is None
    assert findings[0].ai_suggested_severity == "medium"


def test_synthesize_findings_raises_on_malformed_output(stores, sample_image_bytes):
    from ai.ollama_provider import OllamaProvider
    from ai.provider import InvalidAIResponseError
    from ai.report_synthesizer import synthesize_findings
    from ai.schemas import EvidenceAnalysis

    project = stores.evidence_store.create_project("Bad Synth")
    evidence_list = ingest_inputs(stores, project.id, pasted_text=None, uploaded_files=[(sample_image_bytes, "shot.png")])
    evidence = evidence_list[0]
    analysis = EvidenceAnalysis(evidence_id=evidence.id, classification="note", summary="x")

    transport = FakeTransport([
        _tags_response(["llama3"]), _tags_response(["llama3"]),
        {"response": "not json"},
        _tags_response(["llama3"]),
        {"response": "still not json"},
    ])
    provider = OllamaProvider("http://127.0.0.1:11434", transport=transport)

    with pytest.raises(InvalidAIResponseError):
        synthesize_findings([(evidence, analysis)], provider, "llama3", stores.findings_store, stores.evidence_store, project.id)


def test_synthesize_narrative_filters_hallucinated_and_missing_sections(stores, sample_image_bytes):
    from ai.ollama_provider import OllamaProvider
    from ai.report_synthesizer import synthesize_narrative
    from ai.schemas import EvidenceAnalysis

    project = stores.evidence_store.create_project("Narrative Synth")
    evidence_list = ingest_inputs(stores, project.id, pasted_text=None, uploaded_files=[(sample_image_bytes, "shot.png")])
    evidence = evidence_list[0]
    analysis = EvidenceAnalysis(evidence_id=evidence.id, classification="note", summary="x")

    payload = {"Introduction & Objective": "Real content.", "Hallucinated Section": "should be dropped"}
    transport = FakeTransport([_tags_response(["llama3"]), {"response": json.dumps(payload)}])
    provider = OllamaProvider("http://127.0.0.1:11434", transport=transport)

    result = synthesize_narrative(
        [(evidence, analysis)], [], ["Introduction & Objective", "Methodology"], provider, "llama3",
    )
    assert result == {"Introduction & Objective": "Real content."}
    assert "Hallucinated Section" not in result
    assert "Methodology" not in result


# ---------- Full end-to-end pipeline ----------

def test_run_quick_report_full_pipeline(tmp_path, sample_image_bytes):
    from ai.ollama_provider import OllamaProvider

    settings = Settings(
        data_dir=tmp_path / "appdata", export_dir=tmp_path / "appdata" / "exports",
        ollama_text_model="llama3",
    )
    stores = build_stores(settings)

    evidence_analysis_payload = {
        "evidence_id": "placeholder", "classification": "terminal_screenshot",
        "summary": "Shows nmap output revealing an open SSH port.",
        "technical_facts": [{"fact": "Port 22 open", "confidence": 0.9, "source": "OCR text"}],
        "commands": ["nmap -sV 10.0.0.5"], "ips": ["10.0.0.5"], "ports": [22],
        "urls": [], "usernames": [], "credentials": [], "flags": ["flag{quick_report}"],
        "tools": ["nmap"], "security_observations": ["Open SSH exposed"],
        "suggested_report_section": "Reconnaissance", "suggested_caption": "Nmap scan of 10.0.0.5",
        "corrected_text": None, "verification_status": "verified", "uncertainties": [],
    }
    findings_payload = {
        "findings": [{
            "title": "Open SSH port on 10.0.0.5", "description": "Port 22 is open and reachable.",
            "affected_asset": "10.0.0.5", "technical_impact": "Remote access surface.",
            "business_impact": "Potential unauthorized access.", "remediation": "Restrict SSH access.",
            "suggested_severity": "medium", "severity_rationale": "Exposed but not confirmed exploitable.",
            "evidence_ids": [], "uncertainties": [],
        }]
    }
    narrative_payload = {
        "Introduction & Objective": "This assessment identified a single exposed SSH service.",
        "Methodology": "A network scan was performed against the target.",
        "Attack Path / Walkthrough": "Reconnaissance revealed an open port.",
        "Conclusion": "One finding was identified and should be remediated.",
    }

    transport = FakeTransport([
        _tags_response(["llama3"]),
        _tags_response(["llama3"]),
        _tags_response(["llama3"]),
        {"response": json.dumps(evidence_analysis_payload)},
        _tags_response(["llama3"]),
        {"response": json.dumps(findings_payload)},
        _tags_response(["llama3"]),
        {"response": json.dumps(narrative_payload)},
    ])
    stores.ollama = OllamaProvider("http://127.0.0.1:11434", transport=transport)

    result = run_quick_report(
        stores, pasted_text=None, uploaded_files=[(sample_image_bytes, "shot.png")],
        template_type="ctf", output_format="md",
    )

    assert len(result.evidence) == 1
    assert len(result.analyses) == 1
    assert result.analysis_errors == []
    assert len(result.findings) == 1
    assert result.findings[0].severity is None
    assert result.findings[0].ai_suggested_severity == "medium"
    assert result.plan is not None

    assert result.output_path is not None and result.output_path.exists()
    content = result.output_path.read_text()
    assert "Open SSH port on 10.0.0.5" in content
    assert "Not yet approved by reviewer" in content
    assert "This assessment identified a single exposed SSH service." in content

    stores.conn.close()


def test_run_quick_report_degrades_gracefully_when_ollama_offline(tmp_path, sample_image_bytes):
    from ai.ollama_provider import OllamaProvider, TransportError

    settings = Settings(
        data_dir=tmp_path / "appdata", export_dir=tmp_path / "appdata" / "exports",
        ollama_text_model="llama3",
    )
    stores = build_stores(settings)
    stores.ollama = OllamaProvider(
        "http://127.0.0.1:11434", max_retries=0,
        transport=FakeTransport([TransportError("connection refused")]),
    )

    result = run_quick_report(
        stores, pasted_text="some notes", uploaded_files=[(sample_image_bytes, "shot.png")],
        template_type="ctf", output_format="md",
    )

    assert len(result.evidence) == 2
    assert result.analyses == []
    assert len(result.analysis_errors) == 2
    assert result.findings == []
    assert result.output_path is not None and result.output_path.exists()

    stores.conn.close()
