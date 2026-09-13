"""Central logging configuration.

Rule: never log secrets, API keys, or raw evidence content. Log paths and
IDs, not payloads.
"""
from __future__ import annotations

import logging
from pathlib import Path


def configure_logging(data_dir: Path, level: int = logging.INFO) -> logging.Logger:
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "ninjareport.log"

    logger = logging.getLogger("ninjareport")
    logger.setLevel(level)

    if logger.handlers:
        # Idempotent: avoid duplicate handlers if called more than once.
        return logger

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    logger.addHandler(console_handler)

    return logger
