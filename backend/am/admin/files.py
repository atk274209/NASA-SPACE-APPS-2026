"""File helpers shared by backups and the outbox: atomic writes, checksums, safe path resolution."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from am.config import Settings


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    """Write via a temporary name and rename, so a crash never leaves a half-written file under the final name."""
    tmp = path.with_name(path.name + ".partial")
    with tmp.open("wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def resolve_in_data_dir(settings: Settings, relative: str) -> Path:
    """Absolute path for a file path stored relative to the data folder.

    Paths are stored relative so the records stay valid when the data folder moves (for
    example after a restore on another machine). Anything that would resolve outside the
    data folder is refused, so a tampered record cannot point AMC at other files.
    """
    root = settings.data_dir.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise LookupError("file path is outside the data folder")
    return path


def folder_size(path: Path) -> int:
    if not path.is_dir():
        return 0
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
