"""Ollama local AI provider.

Talks to Ollama's REST API (GET /api/tags, POST /api/generate) over plain
HTTP using stdlib urllib — no extra dependency needed. The `transport`
parameter is injectable so tests can simulate server responses without a
live Ollama instance; production code uses `_http_transport` by default.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Callable

from ai.provider import (
    AIProvider,
    AIProviderError,
    HealthStatus,
    InvalidAIResponseError,
    ModelNotAvailableError,
    OfflineModeError,
)

Transport = Callable[[str, str, dict | None, float], dict]


class TransportError(AIProviderError):
    """Raised by a transport on connection failure or timeout."""


def _http_transport(method: str, url: str, payload: dict | None, timeout: float) -> dict:
    """Default transport: real HTTP call via urllib. No third-party deps."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise TransportError(f"Could not reach {url}: {exc}") from exc
    except TimeoutError as exc:
        raise TransportError(f"Timed out reaching {url}: {exc}") from exc

    if not body:
        return {}
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise TransportError(f"Non-JSON response from {url}: {exc}") from exc


class OllamaProvider(AIProvider):
    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = 180,
        max_retries: int = 1,
        transport: Transport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self._transport = transport or _http_transport

    def _call(self, method: str, path: str, payload: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return self._transport(method, url, payload, self.timeout_seconds)
            except TransportError as exc:
                last_error = exc
        raise OfflineModeError(
            f"Ollama unreachable at {self.base_url} after {self.max_retries + 1} attempt(s): {last_error}"
        )

    def health_check(self) -> HealthStatus:
        try:
            result = self._call("GET", "/api/tags")
            models = [m.get("name", "") for m in result.get("models", [])]
            return HealthStatus(healthy=True, base_url=self.base_url, available_models=models)
        except OfflineModeError as exc:
            return HealthStatus(healthy=False, base_url=self.base_url, reason=str(exc))

    def list_models(self) -> list[str]:
        result = self._call("GET", "/api/tags")
        return [m.get("name", "") for m in result.get("models", [])]

    def _ensure_model_available(self, model: str) -> None:
        if not model:
            raise ModelNotAvailableError(
                "No model configured. Set OLLAMA_TEXT_MODEL / OLLAMA_VISION_MODEL."
            )
        available = self.list_models()
        if model not in available:
            raise ModelNotAvailableError(
                f"Model '{model}' is not pulled locally. Run: ollama pull {model} "
                f"(available: {available or 'none'}). NinjaReport AI never downloads "
                f"models automatically."
            )

    def generate_text(self, prompt: str, model: str) -> str:
        self._ensure_model_available(model)
        return self._generate_raw(prompt, model)

    def _generate_raw(self, prompt: str, model: str) -> str:
        """Generate without re-checking model availability. Used internally
        by generate_json's repair loop so a repair attempt costs exactly one
        extra call, not an extra availability check too."""
        result = self._call(
            "POST", "/api/generate",
            {"model": model, "prompt": prompt, "stream": False},
        )
        return result.get("response", "")

    def generate_json(self, prompt: str, model: str, max_repair_attempts: int = 1) -> dict:
        self._ensure_model_available(model)
        raw = self._generate_raw(prompt, model)
        parsed, error = _try_parse_json(raw)
        if parsed is not None:
            return parsed

        attempts_left = max_repair_attempts
        while attempts_left > 0:
            repair_prompt = (
                "Your previous response was not valid JSON. "
                f"Parse error: {error}\n\n"
                "Return ONLY valid JSON, with no explanation, no markdown "
                f"fences, and no extra text. Previous response was:\n{raw}"
            )
            raw = self._generate_raw(repair_prompt, model)
            parsed, error = _try_parse_json(raw)
            if parsed is not None:
                return parsed
            attempts_left -= 1

        raise InvalidAIResponseError(
            f"Model did not return valid JSON after repair attempt(s): {error}",
            raw_response=raw,
        )


def _try_parse_json(text: str) -> tuple[dict | None, str | None]:
    stripped = text.strip()
    # Models sometimes wrap JSON in markdown fences despite instructions.
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:]
        stripped = stripped.strip()
    try:
        return json.loads(stripped), None
    except json.JSONDecodeError as exc:
        return None, str(exc)
