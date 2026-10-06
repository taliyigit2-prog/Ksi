"""Crash-safe file replacement helpers for checkpoints and small state files."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def _fsync_directory(directory: Path) -> None:
    """Best-effort directory flush; some removable filesystems reject it."""
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def atomic_write_bytes(path: str | Path, content: bytes, *, mode: int = 0o600) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".part", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        _fsync_directory(target.parent)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def atomic_replace(source: str | Path, destination: str | Path) -> None:
    """Atomically promote a completed checkpoint within the same directory."""
    origin = Path(source)
    target = Path(destination)
    if origin.parent.resolve() != target.parent.resolve():
        raise ValueError("Atomik değiştirme aynı klasörde yapılmalıdır.")
    with origin.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(origin, target)
    _fsync_directory(target.parent)


def atomic_write_text(
    path: str | Path, content: str, *, encoding: str = "utf-8", mode: int = 0o600
) -> None:
    atomic_write_bytes(path, content.encode(encoding), mode=mode)


def atomic_write_json(path: str | Path, payload: Any, *, mode: int = 0o600) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    atomic_write_text(path, serialized, mode=mode)
