"""Non-destructive import of completed legacy jobs into internal storage.

Only finished jobs are imported automatically: replaying unfinished work with
old absolute checkpoint paths would be unsafe. Other history remains intact.
No original models, source files, outputs or settings are deleted or modified.
"""

import json
import shutil
import sqlite3
from pathlib import Path

from ksi_local.atomic_files import atomic_write_json
from ksi_local.bundle_runtime import digest_file
from ksi_local.internal_storage import validate_internal_path
from ksi_local.workspace_management import relocate_workspace


def _verify_copy(origin, target):
    if (origin / "outputs").is_symlink() or target.is_symlink():
        raise RuntimeError("İş geçişinde sembolik bağlantı kabul edilmez.")
    expected = {}
    for path in origin.rglob("*"):
        if path.is_symlink():
            raise RuntimeError("Kaynak işte sembolik bağlantı var; kaynak korunmuştur.")
        if path.is_file():
            expected[path.relative_to(origin).as_posix()] = (path.stat().st_size, digest_file(path))
    actual = {}
    for path in target.rglob("*"):
        if path.is_symlink():
            raise RuntimeError("İş kopyasında sembolik bağlantı var; kaynak korunmuştur.")
        if path.is_file() and path != target / ".relocation-receipt.json":
            actual[path.relative_to(target).as_posix()] = (path.stat().st_size, digest_file(path))
    expected.pop(".relocation-receipt.json", None)
    if actual != expected:
        raise RuntimeError("İş kopyası kaynakla eşleşmiyor; geçmiş yolu değiştirilmedi.")


def import_completed_jobs(database: Path, source: Path, destination: Path, *, workspace_id: str) -> dict:
    validate_internal_path(destination)
    report = {"schema_version": 1, "source_preserved": True, "imported": [], "pending": [], "source_available": False}
    if database.is_symlink() or not database.is_file() or source.is_symlink() or not source.is_dir() or (source / "jobs").is_symlink():
        return report
    marker = source / ".workspace-id"
    if marker.is_symlink() or not marker.is_file() or marker.stat().st_size > 4096:
        return report
    if json.loads(marker.read_text(encoding="utf-8")).get("workspace_id") != workspace_id:
        return report
    source = source.resolve()
    if source == destination.resolve():
        return report
    report["source_available"] = True
    with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as connection:
        if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise RuntimeError("Eski iş geçmişi bütünlük denetiminden geçmedi.")
        rows = connection.execute("SELECT id, job_directory, status FROM jobs").fetchall()
    ready = []
    for identifier, directory, status in rows:
        raw = Path(directory)
        origin = raw.resolve()
        if not origin.is_relative_to(source / "jobs"):
            continue
        if status != "completed" or raw.is_symlink() or not origin.is_dir() or origin.parent != source / "jobs":
            report["pending"].append(identifier)
            continue
        target = destination / origin.relative_to(source)
        if target.exists():
            # A pre-existing ordinary job is not evidence of a finished import.
            # Interrupted promotions are reused only with our verified receipt.
            receipt = target / ".relocation-receipt.json"
            if receipt.is_symlink() or not receipt.is_file() or receipt.stat().st_size > 65536:
                report["pending"].append(identifier)
                continue
            record = json.loads(receipt.read_text(encoding="utf-8"))
            if record.get("source") != str(origin) or record.get("source_preserved") is not True:
                report["pending"].append(identifier)
                continue
        ready.append((identifier, origin, target))
    required = sum(p.stat().st_size for _, origin, target in ready if not target.exists()
                   for p in origin.rglob("*") if p.is_file() and not p.is_symlink())
    if ready and shutil.disk_usage(destination).free < required + 20 * 1024**3:
        report["pending"].extend(identifier for identifier, _, _ in ready)
        return report
    backup = database.parent / "jobs-before-internal-migration.sqlite3"
    if backup.is_symlink():
        raise RuntimeError("Geçiş yedeği sembolik bağlantı olamaz.")
    if ready and not backup.exists():
        temporary = backup.with_suffix(".sqlite3.part")
        if temporary.exists() or temporary.is_symlink():
            raise RuntimeError("Eski geçiş yedeği korunuyor; üzerine yazılmadı.")
        with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as current, sqlite3.connect(temporary) as archived:
            current.backup(archived)
            if archived.execute("PRAGMA quick_check").fetchone() != ("ok",):
                raise RuntimeError("Geçiş öncesi veritabanı yedeği doğrulanamadı.")
        temporary.chmod(0o600)
        temporary.rename(backup)
    for identifier, origin, target in ready:
        if not target.exists():
            relocate_workspace(origin, target)
        _verify_copy(origin, target)
        # Never rewrite user documents or outputs. The database path is the
        # authoritative root used for completed-job display/export. Old private
        # checkpoints are retained, not trusted as new execution requests.
        with sqlite3.connect(database) as connection:
            updated = connection.execute(
                "UPDATE jobs SET job_directory = ? WHERE id = ? AND job_directory = ? AND status = 'completed'",
                (str(target), identifier, str(origin)),
            )
            if updated.rowcount != 1:
                raise RuntimeError("İş kaydı geçiş sırasında değişti; kaynak ve kopya korunmuştur.")
        report["imported"].append(identifier)
        atomic_write_json(database.parent / "internal-job-migration.local.json", report, mode=0o600)
    return report
