"""Create a small, verifiable rollback copy of an installed KSI Local Studio release."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _copy_tree(source: Path, destination: Path) -> None:
    if not source.is_dir():
        return
    shutil.copytree(
        source,
        destination,
        symlinks=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
    )


def create_release_backup(
    *,
    destination: str | Path,
    app_path: str | Path,
    state_root: str | Path,
    release_version: str,
) -> Path:
    """Back up the app shell, installed source/config and SQLite state atomically.

    The multi-gigabyte Python environments are deliberately not duplicated. They are
    immutable dependencies shared by the old and new source snapshots. The manifest
    records this boundary explicitly.
    """
    target = Path(destination).expanduser().resolve()
    temporary = target.with_name(f".{target.name}.building")
    app = Path(app_path).expanduser().resolve()
    state = Path(state_root).expanduser().resolve()
    if target.exists() or temporary.exists():
        raise FileExistsError("Geri dönüş yedeği hedefi zaten var.")
    if not app.is_dir():
        raise FileNotFoundError(f"KSI Local Studio uygulaması bulunamadı: {app}")
    runtime = state / "runtime"
    if not (runtime / "src/ksi_local").is_dir():
        raise FileNotFoundError("Kurulu KSI Local Studio çalışma kaynakları bulunamadı.")

    temporary.mkdir(parents=True, mode=0o700)
    try:
        _copy_tree(app, temporary / "KSI Local Studio.app")
        _copy_tree(runtime / "src", temporary / "runtime/src")
        _copy_tree(runtime / "config", temporary / "runtime/config")
        identity = runtime / "workspace-id.json"
        if identity.is_file():
            (temporary / "runtime").mkdir(parents=True, exist_ok=True)
            shutil.copy2(identity, temporary / "runtime/workspace-id.json")

        database = state / "jobs.sqlite3"
        database_backup = temporary / "state/jobs.sqlite3"
        if database.is_file():
            database_backup.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(database) as source, sqlite3.connect(
                database_backup
            ) as destination_connection:
                source.backup(destination_connection)
        preferences = state / "preferences.json"
        if preferences.is_file():
            (temporary / "state").mkdir(parents=True, exist_ok=True)
            shutil.copy2(preferences, temporary / "state/preferences.json")

        files: list[dict[str, Any]] = []
        for path in sorted(item for item in temporary.rglob("*") if item.is_file()):
            relative = path.relative_to(temporary).as_posix()
            files.append(
                {
                    "path": relative,
                    "size_bytes": path.stat().st_size,
                    "sha256": _sha256(path),
                }
            )
        manifest = {
            "schema_version": 1,
            "release_version": release_version,
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "scope": {
                "included": [
                    "signed application bundle",
                    "installed Python source",
                    "runtime configuration and workspace identity",
                    "consistent SQLite backup",
                    "local UI preferences when present",
                ],
                "excluded": [
                    "large Python virtual environments (dependencies remain installed)",
                    "SSD media, outputs and model weights",
                ],
            },
            "files": files,
        }
        manifest_path = temporary / "backup-manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary.rename(target)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return target


def verify_release_backup(path: str | Path) -> tuple[bool, tuple[str, ...]]:
    """Verify every file listed in a rollback backup manifest."""
    root = Path(path).expanduser().resolve()
    manifest_path = root / "backup-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, ("Yedek manifesti okunamadı.",)
    failures: list[str] = []
    for item in manifest.get("files", []):
        relative = str(item.get("path") or "")
        candidate = (root / relative).resolve()
        if root not in candidate.parents or not candidate.is_file():
            failures.append(f"Eksik veya geçersiz dosya: {relative}")
            continue
        if candidate.stat().st_size != item.get("size_bytes"):
            failures.append(f"Boyut uyuşmuyor: {relative}")
        elif _sha256(candidate) != item.get("sha256"):
            failures.append(f"SHA-256 uyuşmuyor: {relative}")
    return not failures, tuple(failures)
