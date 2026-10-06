"""One-way migration from the private predecessor to KSI Local Studio.

The predecessor name is intentionally isolated in this module. Current code,
documentation and public package metadata must use canonical KSI identifiers.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from ksi_local.atomic_files import atomic_write_json
from ksi_local.project_metadata import PRODUCT_NAME, STATE_DIRECTORY, WORKSPACE_DIRECTORY


LEGACY_PRODUCT_NAME = "VideoTR"
LEGACY_PACKAGE_NAME = "videotr"
LEGACY_WORKSPACE_DIRECTORY = LEGACY_PRODUCT_NAME
MIGRATION_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class MigrationPaths:
    legacy_state: Path
    state: Path
    legacy_workspace: Path
    workspace: Path


@dataclass(frozen=True)
class MigrationReport:
    schema_version: int
    status: str
    state_action: str
    workspace_action: str
    database_archive: str | None
    updated_job_paths: int
    completed_at: str | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def migration_paths(*, home: Path, mount_point: Path) -> MigrationPaths:
    application_support = home / "Library" / "Application Support"
    return MigrationPaths(
        legacy_state=application_support / LEGACY_PRODUCT_NAME,
        state=application_support / STATE_DIRECTORY,
        legacy_workspace=mount_point / LEGACY_WORKSPACE_DIRECTORY,
        workspace=mount_point / WORKSPACE_DIRECTORY,
    )


def workspace_directory_candidates(mount_point: Path) -> tuple[Path, ...]:
    """Return the canonical directory first and the predecessor only for migration."""
    return (
        mount_point / WORKSPACE_DIRECTORY,
        mount_point / LEGACY_WORKSPACE_DIRECTORY,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_database(path: Path) -> None:
    if not path.is_file():
        return
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        result = connection.execute("PRAGMA quick_check").fetchone()
    if not result or result[0] != "ok":
        raise RuntimeError("Eski iş veritabanı bütünlük kontrolünden geçemedi.")


def inspect_migration(*, home: Path, mount_point: Path) -> MigrationReport:
    paths = migration_paths(home=home, mount_point=mount_point)
    if paths.state.exists() and paths.legacy_state.exists():
        state_action = "conflict"
    elif paths.legacy_state.exists():
        state_action = "rename"
    elif paths.state.exists():
        state_action = "already_migrated"
    else:
        state_action = "missing"

    if paths.workspace.exists() and paths.legacy_workspace.exists():
        workspace_action = "conflict"
    elif paths.legacy_workspace.exists():
        workspace_action = "rename"
    elif paths.workspace.exists():
        workspace_action = "already_migrated"
    else:
        workspace_action = "missing"
    return MigrationReport(
        schema_version=MIGRATION_SCHEMA_VERSION,
        status="blocked" if "conflict" in (state_action, workspace_action) else "ready",
        state_action=state_action,
        workspace_action=workspace_action,
        database_archive=None,
        updated_job_paths=0,
        completed_at=None,
    )


def _archive_database(state: Path) -> Path | None:
    database = state / "jobs.sqlite3"
    if not database.is_file():
        return None
    _validate_database(database)
    archive_directory = state / "archive" / "pre-ksi"
    archive_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    archive = archive_directory / "jobs.sqlite3"
    if archive.exists():
        _validate_database(archive)
        return archive
    temporary = archive.with_suffix(".sqlite3.partial")
    shutil.copy2(database, temporary)
    os.replace(temporary, archive)
    if _sha256(archive) != _sha256(database):
        raise RuntimeError("İş geçmişi arşiv kopyası doğrulanamadı.")
    return archive


def _update_job_paths(database: Path, old_root: Path, new_root: Path) -> int:
    if not database.is_file():
        return 0
    old_prefix = str(old_root)
    new_prefix = str(new_root)
    with sqlite3.connect(database) as connection:
        _validate_database(database)
        cursor = connection.execute(
            """
            UPDATE jobs
               SET job_directory = ? || substr(job_directory, length(?) + 1)
             WHERE job_directory = ? OR job_directory LIKE ?
            """,
            (new_prefix, old_prefix, old_prefix, old_prefix + "/%"),
        )
        connection.commit()
        return max(0, cursor.rowcount)


def apply_migration(*, home: Path, mount_point: Path) -> MigrationReport:
    """Apply an idempotent, same-volume rename after a conflict-free inspection."""
    initial = inspect_migration(home=home, mount_point=mount_point)
    if initial.status != "ready":
        raise RuntimeError("Eski ve yeni KSI yolları aynı anda var; otomatik geçiş durduruldu.")
    paths = migration_paths(home=home, mount_point=mount_point)

    if initial.state_action == "rename":
        paths.state.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.replace(paths.legacy_state, paths.state)
    if initial.workspace_action == "rename":
        os.replace(paths.legacy_workspace, paths.workspace)

    archive = _archive_database(paths.state)
    updated = _update_job_paths(
        paths.state / "jobs.sqlite3",
        paths.legacy_workspace,
        paths.workspace,
    )
    completed_at = datetime.now(UTC).isoformat(timespec="seconds")
    report = MigrationReport(
        schema_version=MIGRATION_SCHEMA_VERSION,
        status="completed",
        state_action=initial.state_action,
        workspace_action=initial.workspace_action,
        database_archive=str(archive) if archive else None,
        updated_job_paths=updated,
        completed_at=completed_at,
    )
    if paths.state.exists():
        atomic_write_json(paths.state / "migration-report.json", report.to_dict())
    return report
