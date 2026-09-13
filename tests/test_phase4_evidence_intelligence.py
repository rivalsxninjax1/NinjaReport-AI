"""Phase 4 acceptance tests: evidence intelligence.

Uses the same FakeTransport pattern as Phase 3 — no live Ollama server
needed. Requires pydantic (already in requirements.txt from Phase 0).
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from ai.cache import AnalysisCache
from ai.evidence_analyzer import analyze_evidence
from ai.ollama_provider import OllamaProvider, TransportError
from ai.provider import InvalidAIResponseError, OfflineModeError
from ai.schemas import EvidenceAnalysis, TechnicalFact
from core.db import get_connection, init_schema
from core.evidence_store import EvidenceStore
from processors.derived_store import DerivedStore


def _tags_response(models: list[str]) -> dict:
    return {"models": [{"name": m} for m in models]}


class FakeTransport:
    def __init__(self, responses: list):
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict | None]] = []

    def __call__(self, method: str, url: str, payload: dict | None, timeout: float) -> dict:
        self.calls.append((method, url, payload))
        if not self.responses:
            raise AssertionError("FakeTransport ran out of scripted responses")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _valid_analysis_payload(evidence_id: str = "EVD-999") -> dict:
    return {
        "evidence_id": evidence_id,
        "classification": "terminal_screenshot",
        "summary": "A terminal showing an open SSH port.",
        "technical_facts": [
            {"fact": "Port 22 is open", "confidence": 0.9, "source": "OCR line 1"}
        ],
        "commands": ["nmap -sV target"],
        "ips": ["10.0.0.5"],
        "ports": [22],
        "urls": [],
        "usernames": [],
        "credentials": [],
        "flags": [],
        "tools": ["nmap"],
        "security_observations": ["Open SSH exposed"],
        "suggested_report_section": "Reconnaissance",
        "suggested_caption": "Nmap scan revealing open SSH port",
        "corrected_text": None,
        "verification_status": "verified",  # deliberately wrong -> must be forced to unverified
        "uncertainties": [],
    }


# ---------- Schema-level enforcement (no provider needed) ----------

def test_verification_status_always_forced_unverified():
    analysis = EvidenceAnalysis(**_valid_analysis_payload())
    assert analysis.verification_status == "unverified"


def test_technical_fact_requires_nonempty_source():
    with pytest.raises(ValidationError):
        TechnicalFact(fact="Port 22 open", confidence=0.9, source="")


def test_technical_fact_confidence_must_be_in_range():
    with pytest.raises(ValidationError):
        TechnicalFact(fact="x", confidence=1.5, source="ocr")


def test_invalid_port_rejected():
    payload = _valid_analysis_payload()
    payload["ports"] = [70000]
    with pytest.raises(ValidationError):
        EvidenceAnalysis(**payload)


def test_valid_ports_accepted():
    payload = _valid_analysis_payload()
    payload["ports"] = [22, 443, 8080]
    analysis = EvidenceAnalysis(**payload)
    assert analysis.ports == [22, 443, 8080]


def test_classification_and_summary_required():
    payload = _valid_analysis_payload()
    payload["classification"] = ""
    with pytest.raises(ValidationError):
        EvidenceAnalysis(**payload)


# ---------- analyze_evidence orchestration ----------

@pytest.fixture
def env(tmp_path):
    conn = get_connection(tmp_path / "db" / "test.sqlite3")
    init_schema(conn)
    evidence_store = EvidenceStore(conn, evidence_root=tmp_path / "evidence")
    derived_store = DerivedStore(conn)
    cache = AnalysisCache(conn)

    project = evidence_store.create_project("Phase 4 Test")
    src = tmp_path / "incoming.txt"
    src.write_bytes(b"terminal output: port 22 open")
    evidence = evidence_store.add_evidence(
        project_id=project.id, source_path=src, original_filename="terminal.txt",
        evidence_type="note", max_upload_mb=50,
    )
    yield {
        "conn": conn,
        "evidence_store": evidence_store,
        "derived_store": derived_store,
        "cache": cache,
        "evidence": evidence,
    }
    conn.close()


def test_analyze_evidence_uses_cache_and_skips_generation(env):
    evidence = env["evidence"]
    cache: AnalysisCache = env["cache"]
    cached_payload = _valid_analysis_payload(evidence_id=evidence.id)
    cache.set(evidence.sha256, "llama3", "v1", cached_payload)

    transport = FakeTransport([])  # any call would raise -> proves cache was used
    provider = OllamaProvider("http://127.0.0.1:11434", transport=transport)

    result = analyze_evidence(
        evidence, env["derived_store"], provider, cache, model="llama3", prompt_version="v1",
    )
    assert result.evidence_id == evidence.id
    assert result.verification_status == "unverified"
    assert len(transport.calls) == 0


def test_analyze_evidence_raises_offline_without_fabricating(env):
    transport = FakeTransport([TransportError("connection refused")])
    provider = OllamaProvider("http://127.0.0.1:11434", max_retries=0, transport=transport)

    with pytest.raises(OfflineModeError):
        analyze_evidence(
            env["evidence"], env["derived_store"], provider, env["cache"],
            model="llama3", prompt_version="v1",
        )
    # Nothing should have been cached from a failed/offline attempt.
    assert env["cache"].get(env["evidence"].sha256, "llama3", "v1") is None


def test_analyze_evidence_overrides_ai_supplied_evidence_id_and_caches(env):
    evidence = env["evidence"]
    wrong_id_payload = _valid_analysis_payload(evidence_id="EVD-WRONG")
    transport = FakeTransport([
        _tags_response(["llama3"]),               # health_check
        _tags_response(["llama3"]),                # generate_json -> ensure_model_available
        {"response": json.dumps(wrong_id_payload)},  # generate_json -> generate
    ])
    provider = OllamaProvider("http://127.0.0.1:11434", transport=transport)

    result = analyze_evidence(
        evidence, env["derived_store"], provider, env["cache"], model="llama3", prompt_version="v1",
    )
    assert result.evidence_id == evidence.id  # never trusts the AI's own claimed ID
    assert env["cache"].get(evidence.sha256, "llama3", "v1") is not None


def test_analyze_evidence_schema_repair_then_success(env):
    evidence = env["evidence"]
    broken_payload = _valid_analysis_payload(evidence_id=evidence.id)
    del broken_payload["summary"]  # missing required field -> ValidationError
    fixed_payload = _valid_analysis_payload(evidence_id=evidence.id)

    transport = FakeTransport([
        _tags_response(["llama3"]),                  # health_check
        _tags_response(["llama3"]),                  # generate_json -> ensure_model_available
        {"response": json.dumps(broken_payload)},    # generate_json -> generate (invalid schema)
        _tags_response(["llama3"]),                  # repair generate_json -> ensure_model_available
        {"response": json.dumps(fixed_payload)},      # repair generate_json -> generate (valid)
    ])
    provider = OllamaProvider("http://127.0.0.1:11434", transport=transport)

    result = analyze_evidence(
        evidence, env["derived_store"], provider, env["cache"], model="llama3", prompt_version="v1",
    )
    assert result.evidence_id == evidence.id
    assert result.summary  # present after repair
    assert env["cache"].get(evidence.sha256, "llama3", "v1") is not None


def test_analyze_evidence_raises_invalid_after_repair_fails(env):
    evidence = env["evidence"]
    broken_payload = _valid_analysis_payload(evidence_id=evidence.id)
    del broken_payload["summary"]
    still_broken_payload = _valid_analysis_payload(evidence_id=evidence.id)
    del still_broken_payload["classification"]

    transport = FakeTransport([
        _tags_response(["llama3"]),
        _tags_response(["llama3"]),
        {"response": json.dumps(broken_payload)},
        _tags_response(["llama3"]),
        {"response": json.dumps(still_broken_payload)},
    ])
    provider = OllamaProvider("http://127.0.0.1:11434", transport=transport)

    with pytest.raises(InvalidAIResponseError):
        analyze_evidence(
            evidence, env["derived_store"], provider, env["cache"], model="llama3", prompt_version="v1",
        )
    assert env["cache"].get(evidence.sha256, "llama3", "v1") is None
