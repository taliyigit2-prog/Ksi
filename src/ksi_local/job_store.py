"""SQLite-backed jobs, stages and sanitized logs for the desktop application."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable

from ksi_local.privacy import redact_sensitive_text, safe_source_reference
from ksi_local.project_metadata import STATE_DIRECTORY


SCHEMA_VERSION = 6


class JobKind(StrEnum):
    VIDEO = "video"
    DOCUMENT = "document"
    MEDIA = "media"
    IMAGE = "image"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_SSD = "waiting_for_ssd"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    FAILED = "failed"
    IMPORTED = "imported"
    EXTRACTED = "extracted"
    TRANSLATED = "translated"
    SUMMARIZED = "summarized"
    COMPLETED = "completed"


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


_JOB_TRANSITIONS = {
    JobStatus.QUEUED: {JobStatus.RUNNING, JobStatus.WAITING_FOR_SSD, JobStatus.CANCELLED, JobStatus.FAILED},
    JobStatus.RUNNING: {
        JobStatus.COMPLETED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
        JobStatus.WAITING_FOR_SSD,
        JobStatus.PAUSED,
        JobStatus.IMPORTED,
        JobStatus.EXTRACTED,
        JobStatus.TRANSLATED,
        JobStatus.SUMMARIZED,
    },
    JobStatus.WAITING_FOR_SSD: {JobStatus.QUEUED, JobStatus.CANCELLED},
    JobStatus.PAUSED: {JobStatus.QUEUED, JobStatus.CANCELLED},
    JobStatus.CANCELLED: {JobStatus.QUEUED},
    JobStatus.FAILED: {JobStatus.QUEUED, JobStatus.CANCELLED},
    JobStatus.IMPORTED: {JobStatus.QUEUED, JobStatus.CANCELLED},
    JobStatus.EXTRACTED: {JobStatus.QUEUED, JobStatus.CANCELLED},
    JobStatus.TRANSLATED: {JobStatus.QUEUED, JobStatus.CANCELLED},
    JobStatus.SUMMARIZED: {JobStatus.QUEUED, JobStatus.CANCELLED},
    JobStatus.COMPLETED: set(),
}

_STAGE_TRANSITIONS = {
    StageStatus.PENDING: {StageStatus.RUNNING, StageStatus.CANCELLED},
    StageStatus.RUNNING: {
        StageStatus.COMPLETED,
        StageStatus.FAILED,
        StageStatus.CANCELLED,
    },
    StageStatus.FAILED: {StageStatus.PENDING, StageStatus.CANCELLED},
    StageStatus.CANCELLED: {StageStatus.PENDING},
    StageStatus.COMPLETED: set(),
}


@dataclass(frozen=True)
class JobRecord:
    id: str
    job_kind: JobKind
    source_kind: str
    source_reference: str
    source_language: str
    want_subtitle: bool
    want_summary: bool
    want_dub: bool
    download_only: bool
    max_height: int
    media_index: int
    summary_profile: str
    document_summary_source: str
    document_summary_pdf: bool
    job_directory: str
    status: JobStatus
    current_stage: str | None
    last_error: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class StageRecord:
    job_id: str
    name: str
    ordinal: int
    status: StageStatus
    completed: int
    total: int
    last_error: str | None


def default_database_path() -> Path:
    override = os.environ.get("KSI_STATE_DIRECTORY")
    root = (
        Path(override).expanduser()
        if override
        else Path.home() / "Library/Application Support" / STATE_DIRECTORY
    )
    # Validate before GUI/CLI/MCP creates its state directory or SQLite file.
    # A leftover environment override must not reintroduce external storage.
    from ksi_local.internal_storage import validate_internal_path
    if not root.is_absolute():
        raise RuntimeError("KSI veri klasörü mutlak bir dahili disk yolu olmalıdır.")
    return validate_internal_path(root) / "jobs.sqlite3"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class JobStore:
    def __init__(self, database_path: str | Path | None = None) -> None:
        self.path = Path(database_path or default_database_path()).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            current_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if current_version > SCHEMA_VERSION:
                raise RuntimeError("İş veritabanı bu KSI Local Studio sürümünden daha yeni.")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    job_kind TEXT NOT NULL DEFAULT 'video',
                    source_kind TEXT NOT NULL,
                    source_reference TEXT NOT NULL,
                    source_language TEXT NOT NULL,
                    want_subtitle INTEGER NOT NULL,
                    want_summary INTEGER NOT NULL,
                    want_dub INTEGER NOT NULL,
                    download_only INTEGER NOT NULL DEFAULT 0,
                    max_height INTEGER NOT NULL DEFAULT 1080,
                    media_index INTEGER NOT NULL DEFAULT 1,
                    summary_profile TEXT NOT NULL DEFAULT 'standard',
                    document_summary_source TEXT NOT NULL DEFAULT 'auto',
                    document_summary_pdf INTEGER NOT NULL DEFAULT 0,
                    job_directory TEXT NOT NULL,
                    status TEXT NOT NULL,
                    current_stage TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS stages (
                    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    completed INTEGER NOT NULL DEFAULT 0,
                    total INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    PRIMARY KEY (job_id, name)
                );
                CREATE TABLE IF NOT EXISTS logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS jobs_status_updated ON jobs(status, updated_at);
                CREATE INDEX IF NOT EXISTS logs_job_id ON logs(job_id, id);
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
            }
            if "download_only" not in columns:
                connection.execute(
                    "ALTER TABLE jobs ADD COLUMN download_only INTEGER NOT NULL DEFAULT 0"
                )
            if "max_height" not in columns:
                connection.execute(
                    "ALTER TABLE jobs ADD COLUMN max_height INTEGER NOT NULL DEFAULT 1080"
                )
            if "media_index" not in columns:
                connection.execute(
                    "ALTER TABLE jobs ADD COLUMN media_index INTEGER NOT NULL DEFAULT 1"
                )
            if "job_kind" not in columns:
                connection.execute(
                    "ALTER TABLE jobs ADD COLUMN job_kind TEXT NOT NULL DEFAULT 'video'"
                )
            if "summary_profile" not in columns:
                connection.execute(
                    "ALTER TABLE jobs ADD COLUMN summary_profile TEXT NOT NULL DEFAULT 'standard'"
                )
            if "document_summary_source" not in columns:
                connection.execute(
                    "ALTER TABLE jobs ADD COLUMN document_summary_source TEXT NOT NULL DEFAULT 'auto'"
                )
            if "document_summary_pdf" not in columns:
                connection.execute(
                    "ALTER TABLE jobs ADD COLUMN document_summary_pdf INTEGER NOT NULL DEFAULT 0"
                )
            connection.execute(
                "UPDATE jobs SET job_kind = 'video' "
                "WHERE job_kind IS NULL OR job_kind NOT IN ('video', 'document', 'media', 'image')"
            )
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    @staticmethod
    def _job(row: sqlite3.Row) -> JobRecord:
        return JobRecord(
            id=row["id"],
            job_kind=JobKind(row["job_kind"]),
            source_kind=row["source_kind"],
            source_reference=row["source_reference"],
            source_language=row["source_language"],
            want_subtitle=bool(row["want_subtitle"]),
            want_summary=bool(row["want_summary"]),
            want_dub=bool(row["want_dub"]),
            download_only=bool(row["download_only"]),
            max_height=int(row["max_height"]),
            media_index=int(row["media_index"]),
            summary_profile=str(row["summary_profile"]),
            document_summary_source=str(row["document_summary_source"]),
            document_summary_pdf=bool(row["document_summary_pdf"]),
            job_directory=row["job_directory"],
            status=JobStatus(row["status"]),
            current_stage=row["current_stage"],
            last_error=row["last_error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def create_job(
        self,
        *,
        job_id: str,
        source: str,
        source_language: str,
        want_subtitle: bool,
        want_summary: bool,
        want_dub: bool,
        job_directory: str | Path,
        download_only: bool = False,
        max_height: int = 1080,
        media_index: int = 1,
        job_kind: JobKind | str = JobKind.VIDEO,
        summary_profile: str = "standard",
        document_summary_source: str = "auto",
        document_summary_pdf: bool = False,
    ) -> JobRecord:
        try:
            normalized_job_kind = JobKind(job_kind)
        except ValueError as error:
            raise ValueError("İş türü video, belge, medya veya görsel olmalıdır.") from error
        if isinstance(max_height, bool) or not isinstance(max_height, int):
            raise ValueError("İndirme yüksekliği tam sayı olmalıdır.")
        if max_height < 144 or max_height > 1080:
            raise ValueError("İndirme yüksekliği 144p ile 1080p arasında olmalıdır.")
        if isinstance(media_index, bool) or not isinstance(media_index, int):
            raise ValueError("Medya sırası tam sayı olmalıdır.")
        if media_index < 1 or media_index > 10:
            raise ValueError("Medya sırası 1 ile 10 arasında olmalıdır.")
        if summary_profile not in {"short", "standard", "detailed"}:
            raise ValueError("Özet profili kısa, standart veya ayrıntılı olmalıdır.")
        if document_summary_source not in {"auto", "source", "translation"}:
            raise ValueError("Belge özet kaynağı otomatik, kaynak veya çeviri olmalıdır.")
        if download_only and (want_subtitle or want_summary or want_dub):
            raise ValueError("Sadece indir işi başka çıktı türleri içeremez.")
        source_kind, reference = safe_source_reference(source)
        timestamp = _now()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO jobs (
                    id, job_kind, source_kind, source_reference, source_language,
                    want_subtitle, want_summary, want_dub, download_only,
                    max_height, media_index, summary_profile, document_summary_source,
                    document_summary_pdf, job_directory,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    job_id,
                    normalized_job_kind,
                    source_kind,
                    reference,
                    source_language,
                    int(want_subtitle),
                    int(want_summary),
                    int(want_dub),
                    int(download_only),
                    max_height,
                    media_index,
                    summary_profile,
                    document_summary_source,
                    int(document_summary_pdf),
                    str(Path(job_directory).expanduser().resolve()),
                    JobStatus.QUEUED,
                    timestamp,
                    timestamp,
                ),
            )
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> JobRecord:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(f"İş bulunamadı: {job_id}")
        return self._job(row)

    def list_jobs(
        self, *, limit: int = 50, job_kind: JobKind | str | None = None
    ) -> list[JobRecord]:
        if limit < 1 or limit > 500:
            raise ValueError("İş geçmişi sınırı 1–500 arasında olmalıdır.")
        normalized_kind: JobKind | None = None
        if job_kind is not None:
            try:
                normalized_kind = JobKind(job_kind)
            except ValueError as error:
                raise ValueError("İş türü video, belge, medya veya görsel olmalıdır.") from error
        with self._connect() as connection:
            if normalized_kind is None:
                rows = connection.execute(
                    "SELECT * FROM jobs ORDER BY updated_at DESC LIMIT ?", (limit,)
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM jobs WHERE job_kind = ? "
                    "ORDER BY updated_at DESC LIMIT ?",
                    (normalized_kind, limit),
                ).fetchall()
        return [self._job(row) for row in rows]

    def transition_job(
        self,
        job_id: str,
        status: JobStatus,
        *,
        current_stage: str | None = None,
        error: str | None = None,
    ) -> JobRecord:
        current = self.get_job(job_id)
        if status != current.status and status not in _JOB_TRANSITIONS[current.status]:
            raise ValueError(f"Geçersiz iş durumu geçişi: {current.status} → {status}")
        safe_error = redact_sensitive_text(error) if error else None
        with self._connect() as connection:
            connection.execute(
                """UPDATE jobs SET status = ?, current_stage = ?, last_error = ?, updated_at = ?
                WHERE id = ?""",
                (status, current_stage, safe_error, _now(), job_id),
            )
        return self.get_job(job_id)

    def claim_queued_job(self, job_id: str, *, stage: str) -> JobRecord:
        """Atomically reserve a queued job for one GUI/CLI/MCP executor."""
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE jobs SET status = ?, current_stage = ?, last_error = NULL,
                updated_at = ? WHERE id = ? AND status = ?""",
                (JobStatus.RUNNING, stage, _now(), job_id, JobStatus.QUEUED),
            )
            if cursor.rowcount != 1:
                raise ValueError("İş başka bir istemci tarafından başlatıldı veya artık sırada değil.")
        return self.get_job(job_id)

    def ensure_stages(self, job_id: str, names: Iterable[str]) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(ordinal), -1) AS maximum FROM stages WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            base = int(row["maximum"]) + 1
            for ordinal, name in enumerate(names):
                if not name:
                    raise ValueError("Aşama adı boş olamaz.")
                connection.execute(
                    """INSERT OR IGNORE INTO stages
                    (job_id, name, ordinal, status) VALUES (?, ?, ?, ?)""",
                    (job_id, name, base + ordinal, StageStatus.PENDING),
                )

    def reset_completed_stage(self, job_id: str, name: str) -> None:
        """Allow deliberate regeneration only while its job is safely queued."""
        job = self.get_job(job_id)
        if job.status is not JobStatus.QUEUED:
            raise ValueError("Tamamlanmış aşama yalnız iş sıradayken yenilenebilir.")
        with self._connect() as connection:
            connection.execute(
                """UPDATE stages SET status = ?, completed = 0, total = 0, last_error = NULL
                WHERE job_id = ? AND name = ? AND status = ?""",
                (StageStatus.PENDING, job_id, name, StageStatus.COMPLETED),
            )

    @staticmethod
    def _stage(row: sqlite3.Row) -> StageRecord:
        return StageRecord(
            job_id=row["job_id"],
            name=row["name"],
            ordinal=row["ordinal"],
            status=StageStatus(row["status"]),
            completed=row["completed"],
            total=row["total"],
            last_error=row["last_error"],
        )

    def get_stage(self, job_id: str, name: str) -> StageRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM stages WHERE job_id = ? AND name = ?", (job_id, name)
            ).fetchone()
        if row is None:
            return None
        return self._stage(row)

    def list_stages(self, job_id: str) -> list[StageRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM stages WHERE job_id = ? ORDER BY ordinal", (job_id,)
            ).fetchall()
        return [self._stage(row) for row in rows]

    def set_stage(
        self,
        job_id: str,
        name: str,
        status: StageStatus,
        *,
        completed: int | None = None,
        total: int | None = None,
        error: str | None = None,
    ) -> StageRecord:
        current = self.get_stage(job_id, name)
        if current is None:
            self.ensure_stages(job_id, [name])
            current = self.get_stage(job_id, name)
        assert current is not None
        if status != current.status and status not in _STAGE_TRANSITIONS[current.status]:
            raise ValueError(f"Geçersiz aşama durumu geçişi: {current.status} → {status}")
        safe_error = redact_sensitive_text(error) if error else None
        done = current.completed if completed is None else completed
        maximum = current.total if total is None else total
        if done < 0 or maximum < 0 or (maximum and done > maximum):
            raise ValueError("Aşama ilerleme değerleri geçersiz.")
        with self._connect() as connection:
            connection.execute(
                """UPDATE stages SET status = ?, completed = ?, total = ?, last_error = ?
                WHERE job_id = ? AND name = ?""",
                (status, done, maximum, safe_error, job_id, name),
            )
            connection.execute(
                "UPDATE jobs SET current_stage = ?, updated_at = ? WHERE id = ?",
                (name if status == StageStatus.RUNNING else None, _now(), job_id),
            )
        result = self.get_stage(job_id, name)
        assert result is not None
        return result

    def update_stage_progress(self, job_id: str, name: str, completed: int, total: int) -> None:
        self.set_stage(
            job_id,
            name,
            StageStatus.RUNNING,
            completed=completed,
            total=total,
        )

    def append_log(self, job_id: str, message: str, *, level: str = "info") -> None:
        safe_message = redact_sensitive_text(message).strip()
        if not safe_message:
            return
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO logs (job_id, created_at, level, message) VALUES (?, ?, ?, ?)",
                (job_id, _now(), level[:16], safe_message[:10_000]),
            )

    def read_logs(self, job_id: str, *, limit: int = 200) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT message FROM logs WHERE job_id = ? ORDER BY id DESC LIMIT ?",
                (job_id, limit),
            ).fetchall()
        return [row["message"] for row in reversed(rows)]

    def recover_interrupted_jobs(self, *, workspace_available: bool) -> int:
        from ksi_local.job_leases import lease_is_active

        target = JobStatus.QUEUED if workspace_available else JobStatus.WAITING_FOR_SSD
        with self._connect() as connection:
            running_ids = [
                row["id"]
                for row in connection.execute(
                    "SELECT id FROM jobs WHERE status = ?", (JobStatus.RUNNING,)
                ).fetchall()
            ]
            if not workspace_available:
                running_ids.extend(
                    row["id"]
                    for row in connection.execute(
                        "SELECT id FROM jobs WHERE status = ?", (JobStatus.QUEUED,)
                    ).fetchall()
                )
            identifiers = sorted(identifier for identifier in set(running_ids)
                                 if not workspace_available or not lease_is_active(self.path.parent, identifier))
            for job_id in identifiers:
                connection.execute(
                    "UPDATE jobs SET status = ?, current_stage = NULL, updated_at = ? WHERE id = ?",
                    (target, _now(), job_id),
                )
                connection.execute(
                    "UPDATE stages SET status = ? WHERE job_id = ? AND status = ?",
                    (StageStatus.PENDING, job_id, StageStatus.RUNNING),
                )
        return len(identifiers)

    def resume_waiting_jobs(self) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE jobs SET status = ?, updated_at = ? WHERE status = ?",
                (JobStatus.QUEUED, _now(), JobStatus.WAITING_FOR_SSD),
            )
        return int(cursor.rowcount)

    def pause_archived_jobs(self, workspace_root: Path) -> int:
        """Keep legacy history, but do not auto-run jobs outside active storage."""
        jobs = workspace_root.resolve() / "jobs"
        count = 0
        with self._connect() as connection:
            rows = connection.execute("SELECT id, job_directory, status FROM jobs").fetchall()
            for row in rows:
                if row["status"] not in {JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.WAITING_FOR_SSD}:
                    continue
                if Path(row["job_directory"]).resolve().is_relative_to(jobs):
                    continue
                connection.execute("UPDATE jobs SET status = ?, current_stage = NULL, updated_at = ? WHERE id = ?",
                                   (JobStatus.PAUSED, _now(), row["id"]))
                connection.execute("UPDATE stages SET status = ? WHERE job_id = ? AND status = ?",
                                   (StageStatus.PENDING, row["id"], StageStatus.RUNNING))
                count += 1
        return count

    def retry_job(self, job_id: str) -> JobRecord:
        current = self.get_job(job_id)
        if current.status not in {
            JobStatus.FAILED,
            JobStatus.CANCELLED,
            JobStatus.PAUSED,
            JobStatus.WAITING_FOR_SSD,
            JobStatus.QUEUED,
            JobStatus.IMPORTED,
            JobStatus.EXTRACTED,
            JobStatus.TRANSLATED,
            JobStatus.SUMMARIZED,
        }:
            raise ValueError("Bu iş mevcut durumunda yeniden başlatılamaz.")
        if current.status != JobStatus.QUEUED:
            self.transition_job(job_id, JobStatus.QUEUED)
        with self._connect() as connection:
            connection.execute(
                """UPDATE stages SET status = ?, last_error = NULL
                WHERE job_id = ? AND status IN (?, ?)""",
                (StageStatus.PENDING, job_id, StageStatus.FAILED, StageStatus.CANCELLED),
            )
        return self.get_job(job_id)

    def extend_job_outputs(
        self,
        job_id: str,
        *,
        want_subtitle: bool = False,
        want_summary: bool = False,
        want_dub: bool = False,
    ) -> JobRecord:
        """Reopen a completed job while preserving every reusable completed stage."""
        current = self.get_job(job_id)
        if current.status is not JobStatus.COMPLETED:
            raise ValueError("Yalnız tamamlanmış bir işe yeni çıktı eklenebilir.")
        subtitle = current.want_subtitle or want_subtitle
        summary = current.want_summary or want_summary
        dub = current.want_dub or want_dub
        if (
            subtitle == current.want_subtitle
            and summary == current.want_summary
            and dub == current.want_dub
        ):
            raise ValueError("Bu iş için yeni bir çıktı türü seçilmedi.")
        with self._connect() as connection:
            connection.execute(
                """UPDATE jobs SET want_subtitle = ?, want_summary = ?, want_dub = ?,
                download_only = 0, status = ?, current_stage = NULL, last_error = NULL,
                updated_at = ? WHERE id = ?""",
                (
                    int(subtitle),
                    int(summary),
                    int(dub),
                    JobStatus.QUEUED,
                    _now(),
                    job_id,
                ),
            )
        return self.get_job(job_id)

    def reopen_after_review(self, job_id: str, stages: Iterable[str]) -> JobRecord:
        """Reopen only downstream stages invalidated by an imported text revision."""
        current = self.get_job(job_id)
        if current.status is not JobStatus.COMPLETED:
            raise ValueError("Yalnız tamamlanmış bir iş düzeltmeden sonra yenilenebilir.")
        requested = tuple(dict.fromkeys(stages))
        allowed = {"tts", "dub_quality", "mux"}
        if not requested or any(name not in allowed for name in requested):
            raise ValueError("Yenilenecek düzeltme aşamaları geçersiz.")
        existing = {stage.name for stage in self.list_stages(job_id)}
        missing = [name for name in requested if name not in existing]
        if missing:
            raise ValueError("İşte yenilenecek dublaj aşaması bulunamadı: " + missing[0])
        with self._connect() as connection:
            connection.executemany(
                """UPDATE stages SET status = ?, completed = 0, total = 0,
                last_error = NULL WHERE job_id = ? AND name = ?""",
                [(StageStatus.PENDING, job_id, name) for name in requested],
            )
            connection.execute(
                """UPDATE jobs SET status = ?, current_stage = NULL, last_error = NULL,
                updated_at = ? WHERE id = ?""",
                (JobStatus.QUEUED, _now(), job_id),
            )
        return self.get_job(job_id)

    def claim_next_job(self, *, workspace_available: bool) -> JobRecord | None:
        if not workspace_available:
            self.recover_interrupted_jobs(workspace_available=False)
            return None
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT id FROM jobs WHERE status = ? ORDER BY created_at LIMIT 1",
                (JobStatus.QUEUED,),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ? AND status = ?",
                (JobStatus.RUNNING, _now(), row["id"], JobStatus.QUEUED),
            )
        return self.get_job(row["id"])
