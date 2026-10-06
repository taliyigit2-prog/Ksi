"""Resolve the configured SSD by identity instead of its changeable display name."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from ksi_local.migration import workspace_directory_candidates
from ksi_local.project_metadata import WORKSPACE_DIRECTORY
from ksi_local.storage import discover_mounted_volumes, validate_selected_workspace
from ksi_local.tool_integrity import pinned_tool_version
from ksi_local.atomic_files import atomic_write_json
from ksi_local.bundle_runtime import OfflinePayload, bundle_root, host_architecture, safe_member, tool_path
from ksi_local.workspace_management import (
    WorkspaceLocation, load_selection, new_internal_selection, save_selection,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
IDENTITY_FILE = PROJECT_ROOT / ".phase1" / "workspace-id.json"


def identity_file() -> Path:
    override = os.environ.get("KSI_IDENTITY_FILE")
    return Path(override).expanduser() if override else IDENTITY_FILE


@dataclass(frozen=True)
class WorkspacePaths:
    root: Path
    jobs: Path
    outputs: Path
    models_ollama: Path
    models_whisper: Path
    yt_dlp: Path
    deno: Path


def resolve_workspace() -> WorkspacePaths:
    selection = load_selection()
    if selection is not None:
        if selection.workspace_location is WorkspaceLocation.EXTERNAL:
            # A missing selected SSD never falls back to internal storage.
            volumes = discover_mounted_volumes()
            if not any(
                volume.volume_uuid == selection.volume_uuid
                and volume.workspace_id == selection.workspace_id
                and volume.writable and volume.internal is False
                and Path(selection.workspace_root).resolve().is_relative_to(Path(volume.mount_point).resolve())
                for volume in volumes
            ):
                raise RuntimeError("Seçili harici çalışma alanı bağlı ve yazılabilir değil.")
        return _selected_paths(Path(selection.workspace_root), selection.workspace_id)
    if not identity_file().exists() and not os.environ.get("KSI_IDENTITY_FILE"):
        selection = new_internal_selection()
        root = Path(selection.workspace_root)
        if root.is_symlink() or (root.exists() and any(root.iterdir())):
            raise RuntimeError("İlk kurulum hedefi boş değil; mevcut kullanıcı verisine dokunulmadı.")
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        atomic_write_json(safe_member(root, ".workspace-id"), {"workspace_id": selection.workspace_id})
        # Persist before model copying: an interrupted install resumes this same
        # workspace rather than generating a new identity over existing files.
        save_selection(selection)
        return _selected_paths(root, selection.workspace_id)
    try:
        identity = json.loads(identity_file().read_text(encoding="utf-8"))
        expected_uuid = str(identity["volume_uuid"])
        expected_workspace_id = str(identity["workspace_id"])
    except (OSError, ValueError, KeyError) as error:
        raise RuntimeError("KSI Local Studio SSD kimlik ayarı okunamadı.") from error

    selected = None
    for volume in discover_mounted_volumes():
        valid, _ = validate_selected_workspace(
            volume,
            expected_volume_uuid=expected_uuid,
            expected_workspace_id=expected_workspace_id,
        )
        if valid:
            selected = volume
            break
    if selected is None:
        raise RuntimeError("KSI Local Studio için ayarlanan harici SSD bağlı ve yazılabilir değil.")

    root = next(
        (candidate for candidate in workspace_directory_candidates(Path(selected.mount_point)) if candidate.is_dir()),
        Path(selected.mount_point) / WORKSPACE_DIRECTORY,
    )
    yt_dlp = root / "tools" / "yt-dlp" / pinned_tool_version("yt-dlp") / "yt-dlp"
    deno = root / "tools" / "deno" / pinned_tool_version("deno") / "deno"
    if not yt_dlp.is_file() or not deno.is_file():
        raise RuntimeError("SSD üzerindeki doğrulanmış yt-dlp veya Deno aracı bulunamadı.")
    return WorkspacePaths(
        root=root,
        jobs=root / "jobs",
        outputs=root / "outputs",
        models_ollama=root / "models" / "ollama",
        models_whisper=root / "models" / "whisper" / "large-v3-turbo-8bit",
        yt_dlp=yt_dlp,
        deno=deno,
    )


def _selected_paths(root: Path, workspace_id: str) -> WorkspacePaths:
    if root.is_symlink():
        raise RuntimeError("Çalışma alanı kökü sembolik bağlantı olamaz.")
    if not root.is_absolute():
        raise RuntimeError("Çalışma alanı yolu mutlak olmalıdır.")
    marker = safe_member(root, ".workspace-id")
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError("Çalışma alanı kimliği okunamadı.") from error
    if not isinstance(data, dict) or data.get("workspace_id") != workspace_id:
        raise RuntimeError("Çalışma alanı kimliği seçilen konumla eşleşmiyor.")
    if not os.access(root, os.W_OK):
        raise RuntimeError("Çalışma alanı yazılabilir değil.")
    for relative in ("jobs", "outputs", "models/ollama", "models/whisper"):
        safe_member(root, relative).mkdir(parents=True, exist_ok=True, mode=0o700)
    resources = bundle_root()
    if resources is not None:
        payload = OfflinePayload.load(resources)
        # Model installation is a first-run background task, never a download.
        payload.install_models(safe_member(root, "models"))
        yt_dlp = Path(payload.component("tool", "yt-dlp"))
        deno = Path(payload.component("tool", "deno"))
    else:
        yt_dlp = Path(tool_path("yt-dlp", required=False) or "yt-dlp")
        deno = Path(tool_path("deno", required=False) or "deno")
    whisper = root / "models/whisper/large-v3-turbo-8bit"
    if resources is not None and host_architecture() == "x86_64":
        whisper = root / "models/whisper/ggml-large-v3-turbo.bin"
    return WorkspacePaths(
        root=root, jobs=root / "jobs", outputs=root / "outputs",
        models_ollama=root / "models/ollama", models_whisper=whisper,
        yt_dlp=yt_dlp, deno=deno,
    )
