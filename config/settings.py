"""Application configuration loaded from environment variables.

Phase 0: no AI/OCR logic here — just paths, limits, and feature flags.
Everything has a safe default so the app can boot with zero configuration.

Loads .env (if present at the project root) into the process environment
before reading anything, via a minimal stdlib parser — no python-dotenv
dependency needed for a handful of KEY=VALUE lines.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(env_path: Path | None = None) -> None:
    """Parse a .env file and set values into os.environ.

    Uses setdefault so real environment variables (e.g. set by a shell
    export or a container) always take precedence over .env — matching
    standard dotenv-loading behavior.
    """
    path = env_path or (_PROJECT_ROOT / ".env")
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def _default_data_dir() -> Path:
    """Return an OS-appropriate default app-data directory (no external deps)."""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "NinjaReportAI"


def _bool_env(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Immutable snapshot of application configuration."""

    data_dir: Path = field(default_factory=_default_data_dir)
    export_dir: Path | None = None

    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_text_model: str = ""
    ollama_vision_model: str = ""

    max_upload_mb: int = 50
    ai_timeout_seconds: int = 180
    ocr_enabled: bool = True
    pdf_export_enabled: bool = True

    @classmethod
    def load(cls, env_path: Path | None = None) -> "Settings":
        _load_dotenv(env_path)

        data_dir_env = os.environ.get("NINJAREPORT_DATA_DIR")
        data_dir = Path(data_dir_env).expanduser() if data_dir_env else _default_data_dir()

        export_dir_env = os.environ.get("NINJAREPORT_EXPORT_DIR")
        export_dir = Path(export_dir_env).expanduser() if export_dir_env else (data_dir / "exports")

        return cls(
            data_dir=data_dir,
            export_dir=export_dir,
            ollama_base_url=os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
            ollama_text_model=os.environ.get("OLLAMA_TEXT_MODEL", ""),
            ollama_vision_model=os.environ.get("OLLAMA_VISION_MODEL", ""),
            max_upload_mb=int(os.environ.get("MAX_UPLOAD_MB", "50")),
            ai_timeout_seconds=int(os.environ.get("AI_TIMEOUT_SECONDS", "180")),
            ocr_enabled=_bool_env("OCR_ENABLED", True),
            pdf_export_enabled=_bool_env("PDF_EXPORT_ENABLED", True),
        )

    def ensure_directories(self) -> None:
        """Create data/export/db/evidence directories if missing. Idempotent."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "db").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "evidence").mkdir(parents=True, exist_ok=True)
        if self.export_dir:
            self.export_dir.mkdir(parents=True, exist_ok=True)

    def db_path(self) -> Path:
        return self.data_dir / "db" / "ninjareport.sqlite3"


def get_settings() -> Settings:
    """Convenience accessor. Re-reads env each call; cheap and side-effect free."""
    return Settings.load()
