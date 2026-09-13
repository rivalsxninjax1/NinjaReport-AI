"""Streaming SHA-256 hashing. Never loads whole files into memory at once —
important on the 8 GB RAM target."""
from __future__ import annotations

import hashlib
from pathlib import Path

_CHUNK_SIZE = 1024 * 1024  # 1 MB


def sha256_file(path: Path) -> str:
    """Return the hex SHA-256 digest of the file at `path`."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()
