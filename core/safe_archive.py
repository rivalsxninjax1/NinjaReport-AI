"""Safe zip extraction — guards against zip-slip (archive entries that
escape the intended extraction directory via '../' or absolute paths).

Built ahead of Phase 12 (project archives / .nra import), which will be
the first real caller of this. Corrupted archives raise a clear error
rather than crashing or partially extracting.
"""
from __future__ import annotations

import zipfile
from pathlib import Path


class UnsafeArchiveError(ValueError):
    """Raised when an archive entry would escape the extraction directory,
    or the archive itself is unreadable/corrupted."""


def safe_extract_zip(archive_path: Path, dest_dir: Path) -> list[Path]:
    """Extract every entry in `archive_path` into `dest_dir`, refusing any
    entry whose resolved path would land outside dest_dir. Returns the list
    of extracted file paths. Raises UnsafeArchiveError on a traversal
    attempt or a corrupted/unreadable archive.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_resolved = dest_dir.resolve()

    try:
        with zipfile.ZipFile(archive_path) as zf:
            names = zf.namelist()
            # Validate every entry before extracting any of them — fail
            # closed rather than leaving a partial, possibly-unsafe extract.
            targets: dict[str, Path] = {}
            for name in names:
                # Normalize both separator styles before joining — a
                # Windows-style traversal entry ('..\\..\\evil.txt') isn't
                # interpreted as a parent-directory reference by pathlib on
                # macOS/Linux, since backslash isn't a separator there.
                normalized = name.replace("\\", "/")
                candidate = (dest_dir / normalized).resolve()
                try:
                    candidate.relative_to(dest_resolved)
                except ValueError as exc:
                    raise UnsafeArchiveError(
                        f"Archive entry escapes extraction directory: {name!r}"
                    ) from exc
                if normalized.startswith("/") or (len(normalized) > 1 and normalized[1] == ":"):
                    # Absolute POSIX or Windows drive path, e.g. '/etc/passwd'
                    # or 'C:/evil'. Path joining above would normally
                    # neutralize this, but reject explicitly for clarity.
                    raise UnsafeArchiveError(f"Archive entry uses an absolute path: {name!r}")
                targets[name] = candidate

            extracted = []
            for name, target in targets.items():
                if name.endswith("/"):
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(name) as src, open(target, "wb") as dst:
                    dst.write(src.read())
                extracted.append(target)
            return extracted

    except zipfile.BadZipFile as exc:
        raise UnsafeArchiveError(f"Corrupted or unreadable archive: {exc}") from exc
