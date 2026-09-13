"""Evidence intelligence: turns extracted text (OCR/PDF) into a structured,
schema-validated EvidenceAnalysis via the local AI provider.

Never fabricates: the prompt explicitly forbids inventing IPs, ports, URLs,
credentials, or commands not present in the extracted text, and pushes
anything uncertain into `uncertainties` instead of guessing. If the model's
JSON doesn't match the schema, we make exactly one schema-repair attempt
before raising — we do not silently coerce or drop data to make it fit.
"""
from __future__ import annotations

import json

from pydantic import ValidationError

from ai.cache import AnalysisCache
from ai.provider import AIProvider, InvalidAIResponseError, OfflineModeError
from ai.schemas import EvidenceAnalysis
from core.models import Evidence
from processors.derived_store import DerivedStore

PROMPT_VERSION = "v1"


def build_analysis_prompt(evidence: Evidence, extracted_text: str) -> str:
    return f"""You are analyzing one piece of evidence from a CTF/VAPT engagement to help prepare a report.

Evidence ID: {evidence.id}
Evidence type: {evidence.evidence_type}
Original filename: {evidence.original_filename}

Extracted text (from OCR or PDF extraction — may contain recognition errors):
---
{extracted_text or "(no extracted text available for this evidence)"}
---

Rules you must follow:
- Only report facts clearly supported by the extracted text above. Never invent IP addresses, ports, URLs, usernames, credentials, tools, or commands that are not present in it.
- If something is unclear, ambiguous, or only partially visible, add a short note to "uncertainties" instead of guessing.
- Every entry in "technical_facts" must include a "source" describing exactly where in the extracted text it came from (e.g. "line 2 of OCR text").
- Do not claim anything is verified — a human reviewer decides that, not you.
- If the extracted text is empty or unusable, say so plainly in "summary" and leave the extraction lists empty.

Return ONLY a JSON object with exactly these keys and no others:
{{
  "classification": "short label, e.g. terminal_screenshot | web_request | network_scan | credential_dump | file_listing | other",
  "summary": "1-3 sentence plain-language summary of what this evidence shows",
  "technical_facts": [{{"fact": "string", "confidence": 0.0, "source": "string"}}],
  "commands": ["string"],
  "ips": ["string"],
  "ports": [0],
  "urls": ["string"],
  "usernames": ["string"],
  "credentials": ["string"],
  "flags": ["string"],
  "tools": ["string"],
  "security_observations": ["string"],
  "suggested_report_section": "e.g. Reconnaissance | Initial Access | Privilege Escalation | Evidence Appendix",
  "suggested_caption": "one sentence caption suitable for a report figure",
  "corrected_text": "your best-effort correction of OCR typos in the extracted text, or null",
  "uncertainties": ["string"]
}}

Return raw JSON only — no markdown code fences, no explanation before or after it.
"""


def analyze_evidence(
    evidence: Evidence,
    derived_store: DerivedStore,
    provider: AIProvider,
    cache: AnalysisCache,
    model: str,
    prompt_version: str = PROMPT_VERSION,
) -> EvidenceAnalysis:
    """Return a schema-validated EvidenceAnalysis for this evidence.

    Raises OfflineModeError if the provider is unreachable — callers should
    catch this and mark analysis as skipped, never fabricate a result.
    Raises InvalidAIResponseError if the model's output still doesn't match
    the schema after one repair attempt.
    """
    cached = cache.get(evidence.sha256, model, prompt_version)
    if cached is not None:
        return EvidenceAnalysis(**cached)

    health = provider.health_check()
    if not health.healthy:
        raise OfflineModeError(f"Cannot analyze evidence: Ollama is offline ({health.reason})")

    extracted_text = derived_store.get_combined_text(evidence.project_id, evidence.id) or ""
    prompt = build_analysis_prompt(evidence, extracted_text)

    raw = provider.generate_json(prompt, model=model, max_repair_attempts=1)
    raw["evidence_id"] = evidence.id  # never trust an AI-supplied evidence_id

    try:
        analysis = EvidenceAnalysis(**raw)
    except ValidationError as first_error:
        raw2 = _attempt_schema_repair(provider, model, raw, first_error)
        raw2["evidence_id"] = evidence.id
        try:
            analysis = EvidenceAnalysis(**raw2)
        except ValidationError as second_error:
            raise InvalidAIResponseError(
                f"AI response did not match the EvidenceAnalysis schema after repair: {second_error}",
                raw_response=json.dumps(raw2),
            ) from second_error

    cache.set(evidence.sha256, model, prompt_version, analysis.model_dump())
    return analysis


def _attempt_schema_repair(
    provider: AIProvider, model: str, previous_raw: dict, error: ValidationError
) -> dict:
    repair_prompt = (
        "Your previous JSON response did not match the required schema.\n"
        f"Validation errors:\n{error}\n\n"
        "Return ONLY corrected JSON with exactly the required keys and correct types "
        "(e.g. ports must be a list of integers, technical_facts must each have "
        "fact/confidence/source). Previous response was:\n"
        f"{json.dumps(previous_raw)}"
    )
    return provider.generate_json(repair_prompt, model=model, max_repair_attempts=0)
