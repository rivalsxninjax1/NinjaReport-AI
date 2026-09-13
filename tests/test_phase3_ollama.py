"""Phase 3 acceptance tests: Ollama provider abstraction.

Uses a fake transport (no real HTTP, no live Ollama server needed) so these
run deterministically anywhere. `scripts/ollama_doctor.py` is the separate,
manual live smoke test against a real Ollama instance.
"""
from __future__ import annotations

import pytest

from ai.cache import AnalysisCache
from ai.ollama_provider import OllamaProvider, TransportError
from ai.provider import InvalidAIResponseError, ModelNotAvailableError, OfflineModeError
from core.db import get_connection, init_schema


def _tags_response(models: list[str]) -> dict:
    return {"models": [{"name": m} for m in models]}


class FakeTransport:
    """Configurable stand-in for the real HTTP transport.

    `responses` is a list of either dicts (returned in order) or exceptions
    (raised in order) — lets tests script exact server behavior, including
    transient failures for retry testing.
    """

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


# ---------- Health check ----------

def test_health_check_reports_healthy_with_models():
    transport = FakeTransport([_tags_response(["llama3", "llava"])])
    provider = OllamaProvider("http://127.0.0.1:11434", transport=transport)

    status = provider.health_check()
    assert status.healthy is True
    assert status.available_models == ["llama3", "llava"]


def test_health_check_reports_unhealthy_without_raising():
    transport = FakeTransport([TransportError("connection refused")])
    provider = OllamaProvider("http://127.0.0.1:11434", max_retries=0, transport=transport)

    status = provider.health_check()  # must not raise
    assert status.healthy is False
    assert "connection refused" in status.reason


# ---------- Retry behavior ----------

def test_retries_once_then_succeeds():
    transport = FakeTransport([
        TransportError("temporary blip"),
        _tags_response(["llama3"]),
    ])
    provider = OllamaProvider("http://127.0.0.1:11434", max_retries=1, transport=transport)

    models = provider.list_models()
    assert models == ["llama3"]
    assert len(transport.calls) == 2


def test_raises_offline_after_exhausting_retries():
    transport = FakeTransport([
        TransportError("down"),
        TransportError("still down"),
    ])
    provider = OllamaProvider("http://127.0.0.1:11434", max_retries=1, transport=transport)

    with pytest.raises(OfflineModeError):
        provider.list_models()
    assert len(transport.calls) == 2


# ---------- Model availability (never auto-downloads) ----------

def test_generate_text_rejects_unpulled_model():
    transport = FakeTransport([_tags_response(["llama3"])])
    provider = OllamaProvider("http://127.0.0.1:11434", transport=transport)

    with pytest.raises(ModelNotAvailableError) as exc_info:
        provider.generate_text("hello", model="mistral")
    assert "ollama pull mistral" in str(exc_info.value)
    # Only the /api/tags call happened — no attempt to auto-pull or generate.
    assert len(transport.calls) == 1


def test_generate_text_rejects_unconfigured_model():
    transport = FakeTransport([])
    provider = OllamaProvider("http://127.0.0.1:11434", transport=transport)
    with pytest.raises(ModelNotAvailableError):
        provider.generate_text("hello", model="")


def test_generate_text_succeeds_with_pulled_model():
    transport = FakeTransport([
        _tags_response(["llama3"]),
        {"response": "hello back"},
    ])
    provider = OllamaProvider("http://127.0.0.1:11434", transport=transport)

    result = provider.generate_text("hi", model="llama3")
    assert result == "hello back"


# ---------- Structured JSON generation + repair ----------

def test_generate_json_parses_clean_response():
    transport = FakeTransport([
        _tags_response(["llama3"]),
        {"response": '{"finding": "open port", "severity": "medium"}'},
    ])
    provider = OllamaProvider("http://127.0.0.1:11434", transport=transport)

    result = provider.generate_json("analyze this", model="llama3")
    assert result == {"finding": "open port", "severity": "medium"}


def test_generate_json_strips_markdown_fences():
    transport = FakeTransport([
        _tags_response(["llama3"]),
        {"response": '```json\n{"ok": true}\n```'},
    ])
    provider = OllamaProvider("http://127.0.0.1:11434", transport=transport)

    result = provider.generate_json("analyze this", model="llama3")
    assert result == {"ok": True}


def test_generate_json_repairs_once_then_succeeds():
    transport = FakeTransport([
        _tags_response(["llama3"]),
        {"response": "not json at all"},
        {"response": '{"fixed": true}'},
    ])
    provider = OllamaProvider("http://127.0.0.1:11434", transport=transport)

    result = provider.generate_json("analyze this", model="llama3", max_repair_attempts=1)
    assert result == {"fixed": True}
    # tags + first generate + repair generate = 3 calls
    assert len(transport.calls) == 3


def test_generate_json_raises_after_repair_fails():
    transport = FakeTransport([
        _tags_response(["llama3"]),
        {"response": "garbage"},
        {"response": "still garbage"},
    ])
    provider = OllamaProvider("http://127.0.0.1:11434", transport=transport)

    with pytest.raises(InvalidAIResponseError) as exc_info:
        provider.generate_json("analyze this", model="llama3", max_repair_attempts=1)
    assert exc_info.value.raw_response == "still garbage"


# ---------- Cache ----------

@pytest.fixture
def cache(tmp_path):
    conn = get_connection(tmp_path / "db" / "test.sqlite3")
    init_schema(conn)
    yield AnalysisCache(conn)
    conn.close()


def test_cache_miss_returns_none(cache):
    assert cache.get("abc123", "llama3", "v1") is None


def test_cache_set_then_get_round_trips(cache):
    cache.set("abc123", "llama3", "v1", {"finding": "open port"})
    assert cache.get("abc123", "llama3", "v1") == {"finding": "open port"}


def test_cache_key_includes_prompt_version(cache):
    cache.set("abc123", "llama3", "v1", {"finding": "v1 result"})
    assert cache.get("abc123", "llama3", "v2") is None  # different prompt version -> miss


def test_cache_key_includes_model(cache):
    cache.set("abc123", "llama3", "v1", {"finding": "llama result"})
    assert cache.get("abc123", "llava", "v1") is None  # different model -> miss


def test_cache_overwrite_updates_value(cache):
    cache.set("abc123", "llama3", "v1", {"finding": "first"})
    cache.set("abc123", "llama3", "v1", {"finding": "second"})
    assert cache.get("abc123", "llama3", "v1") == {"finding": "second"}
