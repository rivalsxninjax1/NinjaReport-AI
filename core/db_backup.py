"""Whole-database backup and restore.

Uses sqlite3's built-in online backup API (Connection.backup), which
produces a consistent snapshot even while the source connection is open —
safer than a raw file copy. Restore never deletes the current database
outright; it's moved aside with a timestamp first (a lightweight version
of "safe deletion with an export-first option" for the database itself).
"""
from __future__ import annotations

import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


def backup_database(db_path: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"ninjareport_backup_{timestamp}.sqlite3"

    source = sqlite3.connect(db_path)
    dest = sqlite3.connect(backup_path)
    try:
        source.backup(dest)
    finally:
        dest.close()
        source.close()

    return backup_path


def list_backups(backup_dir: Path) -> list[Path]:
    if not backup_dir.exists():
        return []
    return sorted(backup_dir.glob("ninjareport_backup_*.sqlite3"), reverse=True)


def restore_database(backup_path: Path, live_db_path: Path) -> Path | None:
    """Restore `backup_path` over `live_db_path`. If a live database
    already exists, it's preserved alongside (never deleted outright) as
    `<name>.before_restore_<timestamp><suffix>`, and that path is returned."""
    preserved_path = None
    if live_db_path.exists():
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        preserved_path = live_db_path.with_name(
            f"{live_db_path.stem}.before_restore_{timestamp}{live_db_path.suffix}"
        )
        shutil.copy2(live_db_path, preserved_path)

    live_db_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup_path, live_db_path)
    return preserved_path
