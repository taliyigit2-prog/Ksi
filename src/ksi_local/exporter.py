"""Verified, collision-free copying of completed artifacts to the Mac."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import re
import shutil
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ksi_local.atomic_files import atomic_write_json


ALLOWED_ARTIFACT_SUFFIXES = {
    ".mp4",
    ".mkv",
    ".mov",
    ".webm",
    ".srt",
    ".vtt",
    ".txt",
    ".md",
    ".pdf",
    ".docx",
    ".json",
    ".wav",
    ".m4a",
}
EXPORT_RESERVE_BYTES = 2 * 1024**3
ICLOUD_LARGE_EXPORT_BYTES = 100 * 1024**2
STALE_PART_SECONDS = 6 * 60 * 60
VIDEO_SUFFIXES = {".mp4", ".mkv", ".mov", ".webm"}
EXPORT_KINDS = {"all", "video", "summary", "subtitle", "translation"}


@dataclass(frozen=True)
class ExportArtifact:
    source: Path
    destination_name: str | None = None


@dataclass(frozen=True)
class _PreparedArtifact:
    source: Path
    destination_name: str
    size_bytes: int
    sha256: str


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def safe_filename(value: str, *, fallback: str = "KSI Local Studio") -> str:
    clean = "".join(
        char if char.isalnum() or char in " .-_()[]" else "_" for char in value
    )
    clean = " ".join(clean.split()).strip(" .")
    return clean[:120] or fallback


def select_job_artifacts(
    job_directory: str | Path,
    *,
    export_kind: str,
    title: str,
) -> list[ExportArtifact]:
    """Select only user-facing final artifacts, never checkpoints or quality internals."""
    if export_kind not in EXPORT_KINDS:
        raise ValueError("Geçersiz Masaüstü kopyalama seçimi.")
    job = Path(job_directory).expanduser().resolve()
    if not job.is_dir():
        raise ValueError("İş klasörü bulunamadı.")
    base = safe_filename(title, fallback=f"KSI Local Studio {job.name[:8]}")
    outputs = job / "outputs"
    document_translation = [
        (outputs / f"belge-turkce{suffix}", f"{base}.tr{suffix}")
        for suffix in (".txt", ".md", ".docx", ".pdf")
    ]
    document_summary = [
        (outputs / f"belge-ozeti{suffix}", f"{base}.ozet{suffix}")
        for suffix in (".md", ".docx", ".pdf")
    ]
    if any(
        path.is_file() and not path.is_symlink()
        for path, _name in (*document_translation, *document_summary)
    ):
        selected: list[ExportArtifact] = []
        if export_kind in {"all", "translation", "subtitle"}:
            selected.extend(
                ExportArtifact(path, name)
                for path, name in document_translation
                if path.is_file() and not path.is_symlink()
            )
        if export_kind in {"all", "summary"}:
            selected.extend(
                ExportArtifact(path, name)
                for path, name in document_summary
                if path.is_file() and not path.is_symlink()
            )
        if not selected:
            label = "Türkçe belge" if export_kind in {"translation", "subtitle"} else "belge özeti"
            raise ValueError(f"Bu işte kopyalanabilir {label} çıktısı bulunamadı.")
        return selected
    source = job / "source"
    dubbed = outputs / "turkce-dublaj.mp4"
    subtitled_candidates = tuple(
        path
        for path in (
            outputs / "turkce-altyazili.mp4",
            outputs / "turkce-altyazili.mkv",
        )
        if path.is_file() and not path.is_symlink()
    )
    source_videos = (
        sorted(
            item
            for item in source.iterdir()
            if item.is_file()
            and not item.is_symlink()
            and item.suffix.casefold() in VIDEO_SUFFIXES
            and item.name.startswith("source")
            and not item.name.endswith((".part", ".partial"))
        )
        if source.is_dir()
        else []
    )
    selected: list[ExportArtifact] = []
    if export_kind in {"all", "video"}:
        has_dub = dubbed.is_file() and not dubbed.is_symlink()
        has_subtitle_video = bool(subtitled_candidates)
        if has_dub:
            label = f"{base}.tr-dublaj.mp4" if has_subtitle_video else f"{base}.tr.mp4"
            selected.append(ExportArtifact(dubbed, label))
        for subtitled in subtitled_candidates:
            label = (
                f"{base}.tr-altyazili{subtitled.suffix}"
                if has_dub
                else f"{base}.tr{subtitled.suffix}"
            )
            selected.append(ExportArtifact(subtitled, label))
        if not has_dub and not has_subtitle_video:
            source_video = next(iter(source_videos), None)
            if source_video is not None:
                selected.append(
                    ExportArtifact(source_video, f"{base}{source_video.suffix}")
                )
    subtitle = outputs / "turkce.srt"
    if export_kind in {"all", "subtitle"} and subtitle.is_file() and not subtitle.is_symlink():
        selected.append(ExportArtifact(subtitle, f"{base}.tr.srt"))
    summary = outputs / "ozet.md"
    if export_kind in {"all", "summary"} and summary.is_file() and not summary.is_symlink():
        selected.append(ExportArtifact(summary, f"{base}.ozet.md"))
    if not selected:
        labels = {
            "video": "final video",
            "subtitle": "Türkçe altyazı",
            "summary": "Türkçe özet",
            "all": "kopyalanabilir sonuç",
        }
        raise ValueError(f"Bu işte {labels[export_kind]} bulunamadı.")
    return selected


def export_total_bytes(artifacts: list[str | Path | ExportArtifact]) -> int:
    total = 0
    for artifact in artifacts:
        source = artifact.source if isinstance(artifact, ExportArtifact) else Path(artifact)
        total += Path(source).expanduser().stat().st_size
    return total


def desktop_uses_icloud(desktop: str | Path) -> bool:
    """Detect the common macOS iCloud Desktop layouts without changing state."""
    desktop_path = Path(desktop).expanduser().resolve()
    lowered = str(desktop_path).casefold()
    if "mobile documents/com~apple~clouddocs" in lowered:
        return True
    cloud_desktop = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/Desktop"
    try:
        return cloud_desktop.exists() and desktop_path.samefile(cloud_desktop)
    except OSError:
        return False


def cleanup_stale_export_parts(
    desktop: str | Path, *, older_than_seconds: int = STALE_PART_SECONDS
) -> int:
    """Remove only old hidden directories created by an interrupted KSI Local Studio export."""
    root = Path(desktop).expanduser().resolve()
    if not root.is_dir():
        return 0
    now = time.time()
    removed = 0
    for item in root.glob(".ksi_local-export-*.part"):
        try:
            if (
                re.fullmatch(
                    r"\.ksi_local-export-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\.part",
                    item.name,
                )
                is not None
                and item.is_dir()
                and not item.is_symlink()
                and now - item.stat().st_mtime >= older_than_seconds
            ):
                shutil.rmtree(item)
                removed += 1
        except OSError:
            continue
    return removed


def _prepare_artifacts(
    artifacts: list[str | Path | ExportArtifact],
) -> list[_PreparedArtifact]:
    prepared: list[_PreparedArtifact] = []
    source_paths: set[Path] = set()
    destination_names: set[str] = set()
    for raw_artifact in artifacts:
        if isinstance(raw_artifact, ExportArtifact):
            raw_path = raw_artifact.source
            requested_name = raw_artifact.destination_name
        else:
            raw_path = Path(raw_artifact)
            requested_name = None
        unresolved = Path(raw_path).expanduser()
        if unresolved.is_symlink():
            raise ValueError("Sembolik bağlantı çıktı olarak kopyalanamaz.")
        source = unresolved.resolve()
        if not source.is_file() or source.suffix.casefold() not in ALLOWED_ARTIFACT_SUFFIXES:
            raise ValueError(f"Geçersiz çıktı dosyası: {source.name}")
        if source in source_paths:
            raise ValueError(f"Aynı çıktı birden fazla kez seçildi: {source.name}")
        destination_name = safe_filename(requested_name or source.name, fallback=source.name)
        if Path(destination_name).suffix.casefold() != source.suffix.casefold():
            raise ValueError(f"Çıktı uzantısı değiştirilemez: {source.name}")
        key = destination_name.casefold()
        if key in destination_names:
            raise ValueError(f"Aynı hedef adı birden fazla kez kullanılamaz: {destination_name}")
        stat = source.stat()
        prepared.append(
            _PreparedArtifact(
                source=source,
                destination_name=destination_name,
                size_bytes=stat.st_size,
                sha256=sha256_file(source),
            )
        )
        source_paths.add(source)
        destination_names.add(key)
    return prepared


def _candidate_destination(desktop: Path, safe_name: str, counter: int) -> Path:
    return desktop / (safe_name if counter == 1 else f"{safe_name} ({counter})")


def _rename_exclusive(source: Path, destination: Path) -> None:
    """Atomically publish without replacing a destination created during a race."""
    if sys.platform == "darwin":
        libc = ctypes.CDLL(None, use_errno=True)
        renamex = libc.renamex_np
        renamex.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
        renamex.restype = ctypes.c_int
        if renamex(os.fsencode(source), os.fsencode(destination), 0x00000004) == 0:
            return
        error_number = ctypes.get_errno()
        if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
            raise FileExistsError(destination)
        if error_number in {
            errno.ENOTSUP,
            getattr(errno, "EOPNOTSUPP", errno.ENOTSUP),
            errno.ENOSYS,
        }:
            # Some removable filesystems mounted by macOS (notably exFAT) reject
            # RENAME_EXCL even though a normal same-volume rename is atomic. The
            # KSI Local Studio SSD destination lives in a private, newly-created job
            # directory, so an explicit collision check is the safest portable
            # fallback available on those filesystems.
            if destination.exists():
                raise FileExistsError(destination)
            source.rename(destination)
            return
        raise OSError(error_number, os.strerror(error_number), destination)
    if destination.exists():
        raise FileExistsError(destination)
    source.rename(destination)


def export_artifacts(
    artifacts: list[str | Path | ExportArtifact],
    *,
    desktop: str | Path,
    folder_name: str,
) -> Path:
    """Copy a stable snapshot, verify it, then atomically reveal the final folder."""
    if not artifacts:
        raise ValueError("Masaüstüne kopyalanacak çıktı bulunamadı.")
    safe_name = safe_filename(folder_name, fallback="KSI Local Studio Çıktısı")[:80]
    unresolved = Path(desktop).expanduser()
    if unresolved.is_symlink():
        raise ValueError("Mac hedef klasörü sembolik bağlantı olamaz.")
    desktop_path = unresolved.resolve()
    if not desktop_path.is_dir():
        raise ValueError("Mac hedef klasörü bulunamadı.")
    try:
        if desktop_path.stat().st_dev != Path.home().resolve().stat().st_dev:
            raise ValueError("Hedef, Mac'in dahili diskinde bir klasör olmalıdır.")
    except OSError as error:
        raise ValueError("Mac hedef klasörü doğrulanamadı.") from error
    cleanup_stale_export_parts(desktop_path)
    prepared = _prepare_artifacts(artifacts)
    required = sum(item.size_bytes for item in prepared) + EXPORT_RESERVE_BYTES
    if shutil.disk_usage(desktop_path).free < required:
        raise OSError("Mac hedefinde çıktı ve 2 GiB güvenlik payı için yeterli alan yok.")

    temporary = desktop_path / f".ksi_local-export-{uuid.uuid4()}.part"
    temporary.mkdir(mode=0o700)
    try:
        manifest_files: list[dict[str, object]] = []
        for artifact in prepared:
            copied = temporary / artifact.destination_name
            shutil.copy2(artifact.source, copied)
            copied_size = copied.stat().st_size
            copied_hash = sha256_file(copied)
            if artifact.source.stat().st_size != artifact.size_bytes:
                raise OSError(f"Kaynak kopyalama sırasında değişti: {artifact.source.name}")
            if sha256_file(artifact.source) != artifact.sha256:
                raise OSError(f"Kaynak kopyalama sırasında değişti: {artifact.source.name}")
            if copied_size != artifact.size_bytes or copied_hash != artifact.sha256:
                raise OSError(f"Kopya SHA-256 doğrulaması başarısız: {artifact.source.name}")
            manifest_files.append(
                {
                    "filename": artifact.destination_name,
                    "size_bytes": artifact.size_bytes,
                    "source_filename": artifact.source.name,
                    "source_sha256": artifact.sha256,
                    "copied_sha256": copied_hash,
                    "sha256": copied_hash,
                }
            )
        atomic_write_json(
            temporary / "kopya-manifest.json",
            {
                "schema_version": 1,
                "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "verification": "size+sha256",
                "source_preserved": True,
                "files": manifest_files,
            },
        )
        counter = 1
        while True:
            destination = _candidate_destination(desktop_path, safe_name, counter)
            try:
                _rename_exclusive(temporary, destination)
                break
            except FileExistsError:
                counter += 1
        return destination
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
