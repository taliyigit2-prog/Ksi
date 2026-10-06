"""Workspace selection and crash-safe, non-destructive relocation.

Machine identity and user paths live outside the source tree.  Relocation never
deletes the source; the caller may remove it only after a separate user decision.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path

from ksi_local.atomic_files import atomic_write_json
from ksi_local.project_metadata import WORKSPACE_DIRECTORY


class ApplicationLocation(StrEnum):
    USER_APPLICATIONS = "user_applications"
    SYSTEM_APPLICATIONS = "system_applications"


class WorkspaceLocation(StrEnum):
    INTERNAL = "internal"
    EXTERNAL = "external"


@dataclass(frozen=True)
class WorkspaceSelection:
    application_location: ApplicationLocation
    workspace_location: WorkspaceLocation
    workspace_root: str
    workspace_id: str
    volume_uuid: str | None = None


@dataclass(frozen=True)
class RelocationResult:
    source: str
    destination: str
    file_count: int
    byte_count: int
    manifest_sha256: str
    source_preserved: bool = True


def default_internal_workspace() -> Path:
    return Path.home() / WORKSPACE_DIRECTORY


def selection_path() -> Path:
    override = os.environ.get("KSI_WORKSPACE_SELECTION_FILE")
    if override:
        return Path(override).expanduser()
    from ksi_local.job_store import default_database_path

    return default_database_path().parent / "workspace-selection.json"


def save_selection(selection: WorkspaceSelection, path: str | Path | None = None) -> Path:
    root = Path(selection.workspace_root).expanduser()
    if not root.is_absolute() or "\x00" in selection.workspace_root:
        raise ValueError("Çalışma alanı yolu mutlak ve güvenli olmalıdır.")
    if selection.workspace_location is WorkspaceLocation.EXTERNAL and not selection.volume_uuid:
        raise ValueError("Harici çalışma alanı için disk UUID'si gereklidir.")
    target = Path(path or selection_path()).expanduser()
    atomic_write_json(target, {"schema_version": 1, **asdict(selection)})
    return target


def load_selection(path: str | Path | None = None) -> WorkspaceSelection | None:
    target = Path(path or selection_path()).expanduser()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        selection = WorkspaceSelection(
            application_location=ApplicationLocation(payload["application_location"]),
            workspace_location=WorkspaceLocation(payload["workspace_location"]),
            workspace_root=str(payload["workspace_root"]),
            workspace_id=str(payload["workspace_id"]),
            volume_uuid=str(payload["volume_uuid"]) if payload.get("volume_uuid") else None,
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    root = Path(selection.workspace_root).expanduser()
    return selection if root.is_absolute() and "\x00" not in selection.workspace_root else None


def new_internal_selection() -> WorkspaceSelection:
    return WorkspaceSelection(
        application_location=ApplicationLocation.USER_APPLICATIONS,
        workspace_location=WorkspaceLocation.INTERNAL,
        workspace_root=str(default_internal_workspace()),
        workspace_id=str(uuid.uuid4()),
    )


def _ordinary_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for candidate in root.rglob("*"):
        if candidate.is_symlink():
            raise ValueError("Çalışma alanı taşımasında sembolik bağlantıya izin verilmez.")
        if candidate.is_file():
            files.append(candidate)
    return sorted(files, key=lambda item: item.relative_to(root).as_posix())


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def relocate_workspace(source: str | Path, destination: str | Path) -> RelocationResult:
    """Copy, resume, verify and atomically promote a workspace.

    A sibling ``.ksi-relocation-*`` staging directory is deliberately retained
    after interruption so the next invocation can resume verified files.
    """
    raw_origin = Path(source).expanduser()
    raw_target = Path(destination).expanduser()
    if raw_origin.is_symlink() or raw_target.is_symlink():
        raise ValueError("Çalışma alanı kökü sembolik bağlantı olamaz.")
    origin = raw_origin.resolve()
    target = raw_target.resolve()
    if not origin.is_dir() or origin == target or target.is_relative_to(origin):
        raise ValueError("Kaynak ve hedef ayrı, normal çalışma alanı klasörleri olmalıdır.")
    if target.exists():
        raise FileExistsError("Hedef çalışma alanı zaten var; üzerine yazılmadı.")
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = target.parent / f".ksi-relocation-{target.name}"
    if stage.is_symlink() or (stage.exists() and not stage.is_dir()):
        raise ValueError("Taşıma sahneleme yolu güvenli bir klasör olmalıdır.")
    stage.mkdir(mode=0o700, exist_ok=True)
    _ordinary_files(stage)
    manifest: list[dict[str, object]] = []
    total = 0
    for item in _ordinary_files(origin):
        relative = item.relative_to(origin)
        copied = stage / relative
        copied.parent.mkdir(parents=True, exist_ok=True)
        if copied.is_symlink():
            raise ValueError("Taşıma hedefinde sembolik bağlantıya izin verilmez.")
        expected_hash = _hash_file(item)
        if not copied.is_file() or copied.stat().st_size != item.stat().st_size or _hash_file(copied) != expected_hash:
            temporary = copied.with_name(f".{copied.name}.part")
            if temporary.is_symlink():
                raise ValueError("Taşıma geçici dosyası sembolik bağlantı olamaz.")
            shutil.copyfile(item, temporary)
            os.chmod(temporary, item.stat().st_mode & 0o777)
            if _hash_file(temporary) != expected_hash:
                raise OSError("Kopyalanan dosyanın SHA-256 doğrulaması başarısız.")
            os.replace(temporary, copied)
        size = item.stat().st_size
        total += size
        manifest.append({"path": relative.as_posix(), "size": size, "sha256": expected_hash})
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")
    manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
    atomic_write_json(stage / ".relocation-receipt.json", {
        "schema_version": 1,
        "source": str(origin),
        "file_count": len(manifest),
        "byte_count": total,
        "manifest_sha256": manifest_hash,
        "source_preserved": True,
    })
    os.replace(stage, target)
    return RelocationResult(str(origin), str(target), len(manifest), total, manifest_hash)
