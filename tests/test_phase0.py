"""Phase 0 acceptance tests: config loading, directory creation, no-secret hygiene."""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from config import Settings, get_settings


def test_settings_load_with_defaults(monkeypatch):
    monkeypatch.delenv("NINJAREPORT_DATA_DIR", raising=False)
    monkeypatch.delenv("MAX_UPLOAD_MB", raising=False)
    settings = get_settings()
    assert isinstance(settings, Settings)
    assert settings.max_upload_mb == 50
    assert settings.ai_timeout_seconds == 180
    assert settings.ocr_enabled is True


def test_settings_load_reads_dotenv_file(tmp_path, monkeypatch):
    """Regression test: .env must actually be parsed into the process, not
    just exist as a template. Caught in the wild when OLLAMA_TEXT_MODEL and
    OLLAMA_VISION_MODEL from .env silently had no effect."""
    for key in ("OLLAMA_TEXT_MODEL", "OLLAMA_VISION_MODEL", "MAX_UPLOAD_MB"):
        monkeypatch.delenv(key, raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "OLLAMA_TEXT_MODEL=llama3.1:8b-instruct-q4_K_M\n"
        "OLLAMA_VISION_MODEL=llava:latest\n"
        "# a comment line, ignored\n"
        "\n"
        "MAX_UPLOAD_MB=25\n"
    )

    settings = Settings.load(env_path=env_file)
    assert settings.ollama_text_model == "llama3.1:8b-instruct-q4_K_M"
    assert settings.ollama_vision_model == "llava:latest"
    assert settings.max_upload_mb == 25


def test_real_env_var_overrides_dotenv_file(tmp_path, monkeypatch):
    """A real environment variable must win over a conflicting .env value."""
    env_file = tmp_path / ".env"
    env_file.write_text("MAX_UPLOAD_MB=25\n")
    monkeypatch.setenv("MAX_UPLOAD_MB", "99")

    settings = Settings.load(env_path=env_file)
    assert settings.max_upload_mb == 99


def test_missing_dotenv_file_does_not_crash(tmp_path, monkeypatch):
    monkeypatch.delenv("MAX_UPLOAD_MB", raising=False)
    settings = Settings.load(env_path=tmp_path / "does_not_exist.env")
    assert settings.max_upload_mb == 50  # falls back to default cleanly


def test_settings_load_reads_dotenv_file(tmp_path, monkeypatch):
    """Regression test: .env must actually be parsed into the process, not
    just exist as a template. Caught in the wild when OLLAMA_TEXT_MODEL and
    OLLAMA_VISION_MODEL from .env silently had no effect."""
    for key in ("OLLAMA_TEXT_MODEL", "OLLAMA_VISION_MODEL", "MAX_UPLOAD_MB"):
        monkeypatch.delenv(key, raising=False)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "OLLAMA_TEXT_MODEL=llama3.1:8b-instruct-q4_K_M\n"
        "OLLAMA_VISION_MODEL=llava:latest\n"
        "# a comment line, ignored\n"
        "\n"
        "MAX_UPLOAD_MB=25\n"
    )

    settings = Settings.load(env_path=env_file)
    assert settings.ollama_text_model == "llama3.1:8b-instruct-q4_K_M"
    assert settings.ollama_vision_model == "llava:latest"
    assert settings.max_upload_mb == 25


def test_real_env_var_overrides_dotenv_file(tmp_path, monkeypatch):
    """A real environment variable must win over a conflicting .env value."""
    env_file = tmp_path / ".env"
    env_file.write_text("MAX_UPLOAD_MB=25\n")
    monkeypatch.setenv("MAX_UPLOAD_MB", "99")

    settings = Settings.load(env_path=env_file)
    assert settings.max_upload_mb == 99


def test_missing_dotenv_file_does_not_crash(tmp_path, monkeypatch):
    monkeypatch.delenv("MAX_UPLOAD_MB", raising=False)
    settings = Settings.load(env_path=tmp_path / "does_not_exist.env")
    assert settings.max_upload_mb == 50  # falls back to default cleanly


def test_settings_respect_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("NINJAREPORT_DATA_DIR", str(tmp_path / "custom"))
    monkeypatch.setenv("MAX_UPLOAD_MB", "10")
    monkeypatch.setenv("OCR_ENABLED", "false")
    settings = get_settings()
    assert settings.data_dir == tmp_path / "custom"
    assert settings.max_upload_mb == 10
    assert settings.ocr_enabled is False


def test_ensure_directories_creates_writable_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("NINJAREPORT_DATA_DIR", str(tmp_path / "app-data"))
    settings = get_settings()
    settings.ensure_directories()

    assert settings.data_dir.exists()
    assert (settings.data_dir / "db").exists()
    assert (settings.data_dir / "evidence").exists()

    probe = settings.data_dir / "write_probe.tmp"
    probe.write_text("ok")
    assert probe.read_text() == "ok"
    probe.unlink()


def test_db_path_is_inside_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("NINJAREPORT_DATA_DIR", str(tmp_path / "app-data"))
    settings = get_settings()
    assert settings.db_path().parent == settings.data_dir / "db"
    assert settings.db_path().suffix == ".sqlite3"


def test_app_module_imports_without_streamlit_running():
    """app.bootstrap() must not require a Streamlit runtime to execute."""
    import app  # noqa: F401

    assert hasattr(app, "bootstrap")
    assert hasattr(app, "main")


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SECRET_PATTERNS = [
    re.compile(r"api[_-]?key\s*=\s*['\"][A-Za-z0-9]{16,}", re.IGNORECASE),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
]
SCAN_EXCLUDE_DIRS = {".venv", ".git", "__pycache__", "data"}


def test_no_hardcoded_secrets_in_tracked_source():
    offenders = []
    for path in PROJECT_ROOT.rglob("*.py"):
        if any(part in SCAN_EXCLUDE_DIRS for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                offenders.append(str(path))
    assert not offenders, f"Possible hardcoded secret in: {offenders}"


def test_gitignore_excludes_data_and_env():
    gitignore = (PROJECT_ROOT / ".gitignore").read_text()
    assert ".env" in gitignore
    assert "data/" in gitignore or "/data/" in gitignore
