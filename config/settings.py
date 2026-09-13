"""Application configuration loaded from environment variables.

Phase 0: no AI/OCR logic here — just paths, limits, and feature flags.
Everything has a safe default so the app can boot with zero configuration.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


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
    def load(cls) -> "Settings":
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
