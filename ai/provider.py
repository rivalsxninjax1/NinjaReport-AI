"""AIProvider abstraction.

Any local AI backend (Ollama today, something else later) implements this
interface. Callers should only ever depend on this module, never on
ai.ollama_provider directly, so swapping backends doesn't ripple outward.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class AIProviderError(Exception):
    """Base class for all AI-provider errors."""


class OfflineModeError(AIProviderError):
    """Raised when the provider is unreachable. Callers should catch this
    specifically and degrade gracefully (mark analysis as skipped/offline),
    never crash the pipeline."""


class ModelNotAvailableError(AIProviderError):
    """Raised when a configured model is not present locally. NinjaReport AI
    never auto-downloads models — the fix is always a manual `ollama pull`."""


class InvalidAIResponseError(AIProviderError):
    """Raised when the model's output could not be parsed as valid JSON even
    after one repair attempt. Carries the raw text for logging/debugging."""

    def __init__(self, message: str, raw_response: str):
        self.raw_response = raw_response
        super().__init__(message)


@dataclass
class HealthStatus:
    healthy: bool
    base_url: str
    reason: str | None = None
    available_models: list[str] | None = None


class AIProvider(ABC):
    @abstractmethod
    def health_check(self) -> HealthStatus:
        """Check connectivity. Must never raise — always returns a status."""

    @abstractmethod
    def list_models(self) -> list[str]:
        """Return locally available model names. Never triggers a download."""

    @abstractmethod
    def generate_text(self, prompt: str, model: str) -> str:
        """Return raw text completion. Raises OfflineModeError,
        ModelNotAvailableError, or AIProviderError on failure."""

    @abstractmethod
    def generate_json(self, prompt: str, model: str, max_repair_attempts: int = 1) -> dict:
        """Return a parsed JSON object. Attempts up to max_repair_attempts
        follow-up prompts asking the model to fix malformed JSON before
        raising InvalidAIResponseError."""
