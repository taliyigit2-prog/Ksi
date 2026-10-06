"""Conservative inventory and removal of regenerable per-job intermediates."""

from __future__ import annotations

import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CleanupCandidate:
    path: Path
    relative_path: str
    size_bytes: int
    reason: str


@dataclass(frozen=True)
class CleanupResult:
    removed_files: int
    removed_bytes: int


def _ordinary_files(root: Path) -> list[Path]:
    return [
        item
        for item in root.rglob("*")
        if item.is_file() and not item.is_symlink() and not item.name.startswith("._")
    ]


def recover_cleanup_quarantines(job_directory: str | Path) -> int:
    """Restore regenerable files hidden by an interrupted pre-delete move."""
    unresolved = Path(job_directory).expanduser()
    if unresolved.is_symlink():
        raise ValueError("İş klasörü sembolik bağlantı olamaz.")
    job = unresolved.resolve()
    restored = 0
    for quarantine in job.glob(".cleanup-quarantine-*"):
        if (
            re.fullmatch(
                r"\.cleanup-quarantine-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}",
                quarantine.name,
            )
            is None
            or not quarantine.is_dir()
            or quarantine.is_symlink()
        ):
            continue
        for item in sorted(_ordinary_files(quarantine), key=lambda path: len(path.parts)):
            relative = item.relative_to(quarantine)
            original = job / relative
            if original.exists():
                continue
            original.parent.mkdir(parents=True, exist_ok=True)
            item.rename(original)
            restored += 1
        try:
            shutil.rmtree(quarantine)
        except OSError:
            pass
    return restored


def cleanup_inventory(job_directory: str | Path) -> tuple[CleanupCandidate, ...]:
    """List only files that can be rebuilt while preserving sources and final results."""
    unresolved = Path(job_directory).expanduser()
    if unresolved.is_symlink():
        raise ValueError("Geçerli bir iş klasörü seçilmelidir.")
    job = unresolved.resolve()
    if not job.is_dir():
        raise ValueError("Geçerli bir iş klasörü seçilmelidir.")
    recover_cleanup_quarantines(job)
    candidates: dict[Path, CleanupCandidate] = {}

    def add(path: Path, reason: str) -> None:
        if not path.is_file() or path.is_symlink():
            return
        resolved = path.resolve()
        if not resolved.is_relative_to(job):
            raise ValueError("Temizlik adayı iş klasörünün dışında.")
        candidates[resolved] = CleanupCandidate(
            path=resolved,
            relative_path=str(resolved.relative_to(job)),
            size_bytes=resolved.stat().st_size,
            reason=reason,
        )

    segment_root = job / "work/dub-segments"
    if segment_root.is_dir() and not segment_root.is_symlink():
        for item in _ordinary_files(segment_root):
            add(item, "Yeniden üretilebilir dublaj segmenti")

    recognized = job / "work/turkce-dublaj.asr.srt"
    if (job / "outputs/turkce-dublaj.kalite.json").is_file():
        add(recognized, "Kalite raporu tamamlanmış yeniden-dinleme metni")

    dubbed_audio = job / "outputs/turkce-dublaj.wav"
    if all(
        (job / "outputs" / name).is_file()
        for name in ("turkce-dublaj.mp4", "turkce-dublaj.kalite.json")
    ):
        add(dubbed_audio, "Final videoya gömülmüş geçici dublaj PCM sesi")

    for item in _ordinary_files(job):
        if item.name.endswith((".part", ".partial", ".checkpoint.json")):
            add(item, "Tamamlanmamış veya yeniden üretilebilir checkpoint")

    return tuple(sorted(candidates.values(), key=lambda item: item.relative_path))


def cleanup_intermediates(
    job_directory: str | Path,
    *,
    expected_relative_paths: tuple[str, ...] | None = None,
) -> CleanupResult:
    """Quarantine the reviewed inventory, then remove it; restore on move failure."""
    unresolved = Path(job_directory).expanduser()
    if unresolved.is_symlink():
        raise ValueError("İş klasörü sembolik bağlantı olamaz.")
    job = unresolved.resolve()
    inventory = cleanup_inventory(job)
    if expected_relative_paths is not None:
        expected = tuple(sorted(expected_relative_paths))
        current = tuple(sorted(item.relative_path for item in inventory))
        if current != expected:
            raise RuntimeError("Temizlik adayları onaydan sonra değişti; yeniden inceleyin.")
    if not inventory:
        return CleanupResult(0, 0)

    quarantine = job / f".cleanup-quarantine-{uuid.uuid4()}"
    quarantine.mkdir(mode=0o700)
    moved: list[tuple[Path, Path]] = []
    try:
        for candidate in inventory:
            destination = quarantine / candidate.relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            candidate.path.rename(destination)
            moved.append((candidate.path, destination))
    except Exception:
        for original, quarantined in reversed(moved):
            try:
                original.parent.mkdir(parents=True, exist_ok=True)
                quarantined.rename(original)
            except OSError:
                pass
        shutil.rmtree(quarantine, ignore_errors=True)
        raise

    removed_bytes = sum(item.size_bytes for item in inventory)
    shutil.rmtree(quarantine)
    segment_root = job / "work/dub-segments"
    try:
        segment_root.rmdir()
    except OSError:
        pass
    return CleanupResult(len(inventory), removed_bytes)
