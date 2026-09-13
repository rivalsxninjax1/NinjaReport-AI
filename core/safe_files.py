"""Filename safety and upload validation.

Rule: evidence is untrusted input. Never trust a filename or extension to
determine what gets executed, and never let a path escape its target
directory.
"""
from __future__ import annotations

import mimetypes
import re
from pathlib import Path

# Extensions that must never be treated as anything other than inert bytes
# on disk. This is a storage-time gate; Phase 10 adds execution-prevention
# hardening on top of this.
DISALLOWED_EXTENSIONS = {
    ".exe", ".sh", ".bat", ".cmd", ".com", ".msi", ".app",
    ".command", ".bin", ".ps1", ".dll", ".so", ".dylib", ".scr", ".jar",
}

_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")


class InvalidEvidenceError(ValueError):
    """Raised when an uploaded file fails validation."""


def sanitize_filename(name: str) -> str:
    """Reduce an arbitrary filename to a safe basename.

    Strips directory components (so '../../etc/passwd' -> 'passwd'),
    null bytes, and any character outside a conservative allowlist.
    """
    if not name:
        return "unnamed"

    name = name.replace("\x00", "")
    # Normalize both separator styles before taking the basename, so
    # Windows-style traversal ('..\\..\\x') is also neutralized.
    normalized = name.replace("\\", "/")
    base = normalized.rsplit("/", 1)[-1]

    base = _SAFE_CHARS.sub("_", base)
    base = base.strip("._") or "unnamed"
    return base[:200]


def guess_mime_type(filename: str) -> str | None:
    mime, _ = mimetypes.guess_type(filename)
    return mime


def validate_upload(path: Path, original_filename: str, max_upload_mb: int) -> None:
    """Raise InvalidEvidenceError if the file fails any safety check.

    Checks: extension is not in the disallowed/executable list, and size is
    within the configured limit. Does not inspect file content (that's a
    Phase 2/10 concern) — this is the fast, cheap gate at upload time.
    """
    suffix = Path(original_filename).suffix.lower()
    if suffix in DISALLOWED_EXTENSIONS:
        raise InvalidEvidenceError(f"File type '{suffix}' is not allowed as evidence.")

    if not path.exists():
        raise InvalidEvidenceError(f"Source file does not exist: {path}")

    size_bytes = path.stat().st_size
    max_bytes = max_upload_mb * 1024 * 1024
    if size_bytes > max_bytes:
        raise InvalidEvidenceError(
            f"File is {size_bytes} bytes, exceeding the {max_upload_mb} MB limit."
        )
    if size_bytes == 0:
        raise InvalidEvidenceError("File is empty.")


def resolve_within(base_dir: Path, candidate: Path) -> Path:
    """Resolve `candidate` and assert it stays inside `base_dir`.

    Defense in depth on top of sanitize_filename: even if a caller builds
    a path incorrectly, we refuse to write outside the evidence directory.
    """
    base_resolved = base_dir.resolve()
    candidate_resolved = candidate.resolve()
    try:
        candidate_resolved.relative_to(base_resolved)
    except ValueError as exc:
        raise InvalidEvidenceError(
            f"Refusing to write outside evidence directory: {candidate}"
        ) from exc
    return candidate_resolved
