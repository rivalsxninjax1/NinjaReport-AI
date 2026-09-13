"""Setup diagnostics: a single function summarizing whether the AI backend
is usable, for the Settings page (Phase 9) and the `ninjareport doctor`
CLI command (Phase 13)."""
from __future__ import annotations

from dataclasses import dataclass

from ai.provider import AIProvider


@dataclass
class Diagnostics:
    base_url: str
    reachable: bool
    reason: str | None
    available_models: list[str]
    text_model: str
    text_model_ready: bool
    vision_model: str
    vision_model_ready: bool


def run_diagnostics(provider: AIProvider, text_model: str, vision_model: str) -> Diagnostics:
    health = provider.health_check()
    available = health.available_models or []
    return Diagnostics(
        base_url=health.base_url,
        reachable=health.healthy,
        reason=health.reason,
        available_models=available,
        text_model=text_model,
        text_model_ready=bool(text_model) and text_model in available,
        vision_model=vision_model,
        vision_model_ready=bool(vision_model) and vision_model in available,
    )


def format_diagnostics(d: Diagnostics) -> str:
    lines = [f"Ollama at {d.base_url}: {'REACHABLE' if d.reachable else 'UNREACHABLE'}"]
    if not d.reachable:
        lines.append(f"  reason: {d.reason}")
        lines.append("  -> Start Ollama (e.g. `ollama serve`) and retry.")
        return "\n".join(lines)

    lines.append(f"  available models: {d.available_models or '(none pulled)'}")
    lines.append(
        f"  text model '{d.text_model or '(unset)'}': "
        f"{'READY' if d.text_model_ready else 'NOT AVAILABLE'}"
    )
    if not d.text_model_ready and d.text_model:
        lines.append(f"    -> run: ollama pull {d.text_model}")
    lines.append(
        f"  vision model '{d.vision_model or '(unset)'}': "
        f"{'READY' if d.vision_model_ready else 'NOT AVAILABLE'}"
    )
    if not d.vision_model_ready and d.vision_model:
        lines.append(f"    -> run: ollama pull {d.vision_model}")
    return "\n".join(lines)
