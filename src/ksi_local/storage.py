"""Read-only external-storage discovery and workspace identity validation."""

from __future__ import annotations

import json
import math
import os
import plistlib
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from ksi_local.migration import workspace_directory_candidates

WORKSPACE_MARKER = ".workspace-id"
GIB = 1024**3
MIN_WORKSPACE_TOTAL_BYTES = 64 * GIB
MIN_FREE_RESERVE_BYTES = 20 * GIB
DEFAULT_MISSING_MODEL_BUDGET_BYTES = 13 * GIB
DEFAULT_MAX_VIDEO_MBPS = 8.0
DEFAULT_AUDIO_MBPS = 0.192
VIDEO_MBPS_BY_HEIGHT = {
    144: 0.35,
    240: 0.60,
    360: 1.00,
    480: 2.00,
    720: 4.00,
    1080: 8.00,
}


@dataclass(frozen=True)
class VolumeInfo:
    mount_point: str
    name: str
    volume_uuid: str | None
    filesystem: str | None
    internal: bool | None
    writable: bool
    total_bytes: int
    free_bytes: int
    workspace_id: str | None = None

    @property
    def suitable_external_workspace(self) -> bool:
        return (
            self.internal is False
            and self.writable
            and self.volume_uuid is not None
            and self.total_bytes >= MIN_WORKSPACE_TOTAL_BYTES
            and self.free_bytes >= MIN_FREE_RESERVE_BYTES
            and Path(self.mount_point).resolve() != Path("/")
        )

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["suitable_external_workspace"] = self.suitable_external_workspace
        return result


@dataclass(frozen=True)
class StorageBudget:
    duration_seconds: float
    estimated_source_bytes: int
    estimated_peak_work_bytes: int
    missing_model_bytes: int
    reserve_bytes: int
    free_bytes: int

    @property
    def required_bytes(self) -> int:
        return self.estimated_peak_work_bytes + self.missing_model_bytes + self.reserve_bytes

    @property
    def fits(self) -> bool:
        return self.free_bytes >= self.required_bytes

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["required_bytes"] = self.required_bytes
        result["fits"] = self.fits
        return result


def estimate_video_storage(
    duration_seconds: float,
    *,
    free_bytes: int,
    missing_model_bytes: int = DEFAULT_MISSING_MODEL_BUDGET_BYTES,
    max_video_mbps: float = DEFAULT_MAX_VIDEO_MBPS,
    audio_mbps: float = DEFAULT_AUDIO_MBPS,
    source_bytes: int | None = None,
    processing_requested: bool = True,
) -> StorageBudget:
    """Conservative download/work estimate for preflight decisions."""
    if duration_seconds <= 0 or duration_seconds > 3 * 60 * 60:
        raise ValueError("Süre sıfırdan büyük ve en fazla üç saat olmalıdır.")
    if min(free_bytes, missing_model_bytes) < 0:
        raise ValueError("Disk ve model boyutları negatif olamaz.")
    if source_bytes is not None and source_bytes <= 0:
        raise ValueError("Bilinen kaynak boyutu sıfırdan büyük olmalıdır.")

    encoded_bytes_per_second = (max_video_mbps + audio_mbps) * 1_000_000 / 8
    estimated_source = source_bytes or math.ceil(duration_seconds * encoded_bytes_per_second)
    # Source + final video + a temporary mux copy, two PCM-like audio work files,
    # and 2 GiB fixed safety for fragmented downloads and metadata.
    if processing_requested:
        audio_work = math.ceil(duration_seconds * 192_000 * 2)
        peak_work = math.ceil(estimated_source * 2.25 + audio_work + 2 * GIB)
    else:
        # yt-dlp may temporarily hold separate audio/video streams plus fragments.
        peak_work = math.ceil(estimated_source * 1.35 + 1 * GIB)
    return StorageBudget(
        duration_seconds=duration_seconds,
        estimated_source_bytes=estimated_source,
        estimated_peak_work_bytes=peak_work,
        missing_model_bytes=missing_model_bytes,
        reserve_bytes=MIN_FREE_RESERVE_BYTES,
        free_bytes=free_bytes,
    )


def conservative_video_mbps(max_height: int) -> float:
    """Return the conservative bitrate ceiling for an offered height."""
    if isinstance(max_height, bool) or not isinstance(max_height, int) or max_height <= 0:
        raise ValueError("Görüntü yüksekliği pozitif bir tam sayı olmalıdır.")
    for height, bitrate in VIDEO_MBPS_BY_HEIGHT.items():
        if max_height <= height:
            return bitrate
    return DEFAULT_MAX_VIDEO_MBPS


def _diskutil_info(mount_point: Path) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            ["diskutil", "info", "-plist", str(mount_point)],
            check=False,
            capture_output=True,
            timeout=5,
            shell=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {}
    if completed.returncode != 0:
        return {}
    try:
        payload = plistlib.loads(completed.stdout)
    except plistlib.InvalidFileException:
        return {}
    return payload if isinstance(payload, dict) else {}


def read_workspace_id(mount_point: Path) -> str | None:
    for directory in workspace_directory_candidates(mount_point):
        marker = directory / WORKSPACE_MARKER
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
        except (FileNotFoundError, PermissionError, OSError, json.JSONDecodeError):
            continue
        workspace_id = payload.get("workspace_id") if isinstance(payload, dict) else None
        if isinstance(workspace_id, str) and workspace_id:
            return workspace_id
    return None


def inspect_volume(mount_point: Path) -> VolumeInfo:
    resolved = mount_point.resolve()
    stat = os.statvfs(resolved)
    info = _diskutil_info(resolved)

    internal_value = info.get("Internal")
    internal = internal_value if isinstance(internal_value, bool) else None
    volume_uuid = info.get("VolumeUUID")
    filesystem = info.get("FilesystemType") or info.get("FileSystemPersonality")
    name = info.get("VolumeName") or mount_point.name

    return VolumeInfo(
        mount_point=str(resolved),
        name=str(name),
        volume_uuid=str(volume_uuid) if volume_uuid else None,
        filesystem=str(filesystem) if filesystem else None,
        internal=internal,
        writable=os.access(resolved, os.W_OK),
        total_bytes=stat.f_frsize * stat.f_blocks,
        free_bytes=stat.f_frsize * stat.f_bavail,
        workspace_id=read_workspace_id(resolved),
    )


def discover_mounted_volumes(volumes_root: Path = Path("/Volumes")) -> list[VolumeInfo]:
    """Inspect mounted volumes without creating or modifying any path."""
    if not volumes_root.exists():
        return []

    volumes: list[VolumeInfo] = []
    for entry in sorted(volumes_root.iterdir(), key=lambda item: item.name.casefold()):
        try:
            if entry.resolve() == Path("/"):
                continue
            volumes.append(inspect_volume(entry))
        except (FileNotFoundError, PermissionError, OSError):
            continue
    return volumes


def validate_selected_workspace(
    volume: VolumeInfo,
    *,
    expected_volume_uuid: str,
    expected_workspace_id: str,
) -> tuple[bool, tuple[str, ...]]:
    """Validate identity without falling back to a similarly named volume."""
    errors: list[str] = []
    if volume.internal is not False:
        errors.append("Seçilen disk harici olarak doğrulanamadı.")
    if not volume.writable:
        errors.append("Seçilen disk yazılabilir değil.")
    if volume.volume_uuid != expected_volume_uuid:
        errors.append("Volume UUID beklenen diskle eşleşmiyor.")
    if volume.workspace_id != expected_workspace_id:
        errors.append("KSI çalışma alanı kimliği eşleşmiyor.")
    return not errors, tuple(errors)


def volumes_to_json(volumes: Iterable[VolumeInfo]) -> str:
    return json.dumps([volume.to_dict() for volume in volumes], ensure_ascii=False, indent=2)
