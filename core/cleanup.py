"""Startup cleanup for temporary/scratch artifacts.

Scratch files (e.g. uploads written to disk before EvidenceStore.add_evidence
copies them into permanent storage) are never meant to survive an app
restart — if the app crashed mid-upload, whatever's left there is
guaranteed orphaned garbage. This never touches evidence, derived, or
export directories — only the designated scratch area.
"""
from __future__ import annotations

import shutil
from pathlib import Path


def clean_scratch_dir(scratch_dir: Path) -> int:
    """Remove everything under scratch_dir. Returns the number of top-level
    entries removed. Safe to call on a directory that doesn't exist yet."""
    if not scratch_dir.exists():
        return 0

    removed = 0
    for entry in scratch_dir.iterdir():
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)
        removed += 1
    return removed
