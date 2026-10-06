"""Explicit workspace changes preserve source data and mounted-volume identity."""

import json
import uuid
from pathlib import Path

from ksi_local.atomic_files import atomic_write_json
from ksi_local.storage import discover_mounted_volumes
from ksi_local.workspace_access import workspace_access
from ksi_local.workspace_management import ApplicationLocation, WorkspaceLocation, WorkspaceSelection, relocate_workspace, save_selection


def marker_identity(root: Path) -> str:
    marker = root / ".workspace-id"
    if marker.is_symlink() or not marker.is_file() or marker.stat().st_size > 4096:
        raise ValueError("Seçilen klasör doğrulanabilir bir KSI çalışma alanı değil.")
    try:
        identifier = json.loads(marker.read_text(encoding="utf-8"))["workspace_id"]
        if not isinstance(identifier, str):
            raise ValueError("Çalışma alanı kimliği metin olmalıdır.")
        uuid.UUID(identifier)
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Çalışma alanı kimliği geçersiz.") from error
    return identifier


def change_workspace(target: Path, *, relocate_from: Path | None = None, volumes=None) -> WorkspaceSelection:
    target = target.expanduser().absolute()
    if target == Path("/") or target.is_symlink() or any(parent.is_symlink() for parent in target.parents):
        raise ValueError("Yeni çalışma alanı normal, mutlak ve ayrı bir klasör olmalıdır.")
    with workspace_access(mutation=True):
        discovered = discover_mounted_volumes() if volumes is None else volumes
        external = [volume for volume in discovered if volume.internal is False and target.is_relative_to(Path(volume.mount_point).resolve())]
        volume = max(external, key=lambda item: len(item.mount_point)) if external else None
        if target.is_relative_to(Path("/Volumes")) and volume is None:
            raise RuntimeError("Seçilen harici disk bağlı ve kimliği doğrulanabilir değil.")
        if volume is not None and (not volume.suitable_external_workspace or target == Path(volume.mount_point).resolve()):
            raise RuntimeError("Harici çalışma alanı yazılabilir, UUID’li ve yeterli boş alana sahip bir disk alt klasörü olmalıdır.")
        if relocate_from is not None:
            origin = relocate_from.expanduser().absolute()
            expected = marker_identity(origin)
            if origin == target:
                raise ValueError("Taşıma kaynağı ve hedefi aynı olamaz.")
            relocate_workspace(origin, target)
            if marker_identity(target) != expected:
                raise RuntimeError("Taşınan çalışma alanı kimliği eşleşmiyor; kaynak korunmuştur.")
        elif target.exists():
            if not target.is_dir():
                raise ValueError("Çalışma alanı hedefi normal klasör olmalıdır.")
            if any(target.iterdir()):
                marker_identity(target)  # Unrelated populated folders are never adopted.
            else:
                atomic_write_json(target / ".workspace-id", {"workspace_id": str(uuid.uuid4())})
        else:
            target.mkdir(parents=True, mode=0o700)
            atomic_write_json(target / ".workspace-id", {"workspace_id": str(uuid.uuid4())})
        selection = WorkspaceSelection(application_location=ApplicationLocation.USER_APPLICATIONS,
            workspace_location=WorkspaceLocation.EXTERNAL if volume else WorkspaceLocation.INTERNAL,
            workspace_root=str(target), workspace_id=marker_identity(target), volume_uuid=volume.volume_uuid if volume else None)
        save_selection(selection)
        return selection
