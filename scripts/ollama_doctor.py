#!/usr/bin/env python3
"""Live smoke test against your actual local Ollama server.

Run this after starting Ollama to confirm NinjaReport AI can reach it,
see which models are pulled, and (optionally) run one real generation.
This talks to a REAL server — it is not part of the automated test suite.

Usage:
    python scripts/ollama_doctor.py
    python scripts/ollama_doctor.py --generate "Say hello in five words."
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai.diagnostics import format_diagnostics, run_diagnostics
from ai.ollama_provider import OllamaProvider
from ai.provider import AIProviderError
from config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="NinjaReport AI / Ollama doctor")
    parser.add_argument("--generate", help="Send a test prompt to the configured text model")
    args = parser.parse_args()

    settings = get_settings()
    provider = OllamaProvider(
        base_url=settings.ollama_base_url,
        timeout_seconds=settings.ai_timeout_seconds,
    )

    diagnostics = run_diagnostics(provider, settings.ollama_text_model, settings.ollama_vision_model)
    print(format_diagnostics(diagnostics))

    if args.generate:
        if not diagnostics.reachable:
            print("\nSkipping generation test: Ollama is unreachable.")
            sys.exit(1)
        if not diagnostics.text_model_ready:
            print(f"\nSkipping generation test: text model '{settings.ollama_text_model}' not ready.")
            sys.exit(1)
        print(f"\nSending prompt to {settings.ollama_text_model}...")
        try:
            response = provider.generate_text(args.generate, settings.ollama_text_model)
            print("Response:", response)
        except AIProviderError as exc:
            print("Generation failed:", exc)
            sys.exit(1)


if __name__ == "__main__":
    main()
