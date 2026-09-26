"""Synthesizes findings and narrative section text from a batch of
per-evidence AI analyses.

Same hard rules as everywhere else in this codebase: an AI-suggested
severity never becomes a finding's effective severity by itself
(FindingsStore.suggest_severity only — approval is a separate human
action), and an AI reference to an evidence_id that doesn't actually
exist in this batch is silently dropped rather than trusted.
"""
from __future__ import annotations

import json

from pydantic import ValidationError

from ai.provider import AIProvider, InvalidAIResponseError
from ai.schemas import EvidenceAnalysis, FindingsSynthesis
from core.evidence_store import EvidenceStore
from core.findings_store import VALID_SEVERITIES, FindingsStore
from core.models import Evidence, Finding

FINDINGS_PROMPT_VERSION = "v1"


def synthesize_findings(
    evidence_analyses: list[tuple[Evidence, EvidenceAnalysis]],
    provider: AIProvider,
    model: str,
    findings_store: FindingsStore,
    evidence_store: EvidenceStore,
    project_id: str,
) -> list[Finding]:
    """Ask the AI to propose findings from a batch of evidence analyses,
    then create them (with suggested-but-unapproved severity). Returns the
    created Finding records. Returns [] if there's nothing to analyze or
    the AI produced nothing usable — never raises for "no findings found",
    only for a genuine provider failure."""
    if not evidence_analyses:
        return []

    valid_evidence_ids = {evidence.id for evidence, _ in evidence_analyses}
    prompt = _build_findings_prompt(evidence_analyses)

    raw = provider.generate_json(prompt, model=model, max_repair_attempts=1)
    try:
        synthesis = FindingsSynthesis(**raw)
    except ValidationError as exc:
        raise InvalidAIResponseError(
            f"Findings synthesis did not match the expected schema: {exc}",
            raw_response=json.dumps(raw),
        ) from exc

    created: list[Finding] = []
    for draft in synthesis.findings:
        filtered_evidence_ids = [eid for eid in draft.evidence_ids if eid in valid_evidence_ids]

        finding = findings_store.create_finding(
            project_id,
            title=draft.title,
            affected_asset=draft.affected_asset,
            description=draft.description,
            technical_impact=draft.technical_impact,
            business_impact=draft.business_impact,
            remediation=draft.remediation,
            evidence_ids=filtered_evidence_ids,
            evidence_store=evidence_store,
            verification_notes="Drafted automatically by Quick Report; not yet human-reviewed.",
        )

        if draft.suggested_severity in VALID_SEVERITIES:
            findings_store.suggest_severity(
                project_id, finding.id, draft.suggested_severity, draft.severity_rationale,
            )
            finding = findings_store.get_finding(project_id, finding.id)

        created.append(finding)

    return created


def _build_findings_prompt(evidence_analyses: list[tuple[Evidence, EvidenceAnalysis]]) -> str:
    evidence_summaries = []
    for evidence, analysis in evidence_analyses:
        evidence_summaries.append(
            f"- {evidence.id} ({analysis.classification}): {analysis.summary}\n"
            f"  security_observations: {analysis.security_observations}\n"
            f"  tools: {analysis.tools}, ips: {analysis.ips}, ports: {analysis.ports}"
        )
    joined = "\n".join(evidence_summaries)

    return f"""You are drafting security findings from a batch of already-analyzed evidence for a CTF/VAPT report.

Evidence summaries:
{joined}

Rules:
- Only propose findings clearly supported by the evidence summaries above. Do not invent details not present in them.
- Every finding's "evidence_ids" must be drawn ONLY from the evidence IDs listed above.
- If evidence is too sparse or unclear to support a finding, don't force one — return fewer findings rather than a weak one.
- Suggest a severity (informational, low, medium, high, critical) with a one-sentence rationale, but never claim it is approved or verified — a human reviewer decides that.
- If something about a finding is uncertain, note it in "uncertainties" instead of guessing.

Return ONLY a JSON object with exactly this shape:
{{
  "findings": [
    {{
      "title": "string",
      "description": "string",
      "affected_asset": "string",
      "technical_impact": "string",
      "business_impact": "string",
      "remediation": "string",
      "suggested_severity": "informational|low|medium|high|critical",
      "severity_rationale": "string",
      "evidence_ids": ["EVD-..."],
      "uncertainties": ["string"]
    }}
  ]
}}

Return raw JSON only — no markdown fences, no explanation before or after it.
"""


def synthesize_narrative(
    evidence_analyses: list[tuple[Evidence, EvidenceAnalysis]],
    findings: list[Finding],
    section_titles: list[str],
    provider: AIProvider,
    model: str,
) -> dict[str, str]:
    """Draft narrative paragraphs for the given (fixed, known-safe) section
    titles. Returns only sections the AI actually filled in with a
    non-empty string — never fabricates a key that wasn't asked for, and
    silently drops anything else the model might have hallucinated."""
    if not section_titles:
        return {}

    evidence_summaries = "\n".join(
        f"- {evidence.id} ({analysis.classification}): {analysis.summary}"
        for evidence, analysis in evidence_analyses
    )
    findings_summaries = "\n".join(
        f"- {finding.title}: {finding.description}" for finding in findings
    ) or "(no findings drafted)"

    sections_json_shape = ", ".join(f'"{title}": "string"' for title in section_titles)

    prompt = f"""You are writing narrative sections for a security report, based on already-analyzed evidence and drafted findings.

Evidence summaries:
{evidence_summaries}

Draft findings:
{findings_summaries}

Write one paragraph of plain, professional prose for EACH of these exact section titles: {section_titles}.
Only state what the evidence and findings above actually support — do not invent details.

Return ONLY a JSON object with exactly these keys: {{{sections_json_shape}}}

Return raw JSON only — no markdown fences, no explanation before or after it.
"""

    raw = provider.generate_json(prompt, model=model, max_repair_attempts=1)
    if not isinstance(raw, dict):
        return {}

    return {
        title: text for title, text in raw.items()
        if title in section_titles and isinstance(text, str) and text.strip()
    }
