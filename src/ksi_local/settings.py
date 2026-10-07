"""Resolve an internal workspace; legacy external state is archived, not erased."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from ksi_local.atomic_files import atomic_write_json
from ksi_local.internal_storage import validate_internal_path
from ksi_local.bundle_runtime import OfflinePayload, bundle_root, host_architecture, safe_member, tool_path
from ksi_local.workspace_management import (
    WorkspaceLocation, load_selection, new_internal_selection, save_selection,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
IDENTITY_FILE = PROJECT_ROOT / ".phase1" / "workspace-id.json"


def identity_file() -> Path:
    override = os.environ.get("KSI_IDENTITY_FILE")
    if override:
        return Path(override).expanduser()
    if bundle_root() is not None:
        # Read identity only, never copy the old personal runtime into a build.
        # A missing previously selected SSD must not initialize a new fallback.
        legacy = Path.home() / "Library/Application Support/KSI Local Studio/runtime/workspace-id.json"
        if legacy.is_file() and not legacy.is_symlink():
            return legacy
    return IDENTITY_FILE


@dataclass(frozen=True)
class WorkspacePaths:
    root: Path
    jobs: Path
    outputs: Path
    models_ollama: Path
    models_whisper: Path
    yt_dlp: Path
    deno: Path


def resolve_workspace(*, initialize: bool = False) -> WorkspacePaths:
    from ksi_local.workspace_access import workspace_access
    selection = load_selection()
    initial_creation = initialize and (selection is None or selection.workspace_location is WorkspaceLocation.EXTERNAL)
    with workspace_access(mutation=initial_creation):
        return _resolve_workspace(initialize=initialize)


def _resolve_workspace(*, initialize: bool = False) -> WorkspacePaths:
    selection = load_selection()
    if selection is not None and selection.workspace_location is WorkspaceLocation.EXTERNAL:
        if not initialize:
            raise RuntimeError("Eski harici çalışma alanı kaydı korunuyor; dahili kurulumu uygulamadan tamamlayın.")
        from dataclasses import asdict
        from ksi_local.workspace_management import selection_path

        # Immutable original configuration remains private. No external disk
        # has to be connected, and no legacy model/job/source is modified.
        archive = selection_path().parent / "legacy-external-workspace.local.json"
        original = {"schema_version": 1, **asdict(selection)}
        if archive.exists():
            if archive.is_symlink() or json.loads(archive.read_text(encoding="utf-8")) != original:
                raise RuntimeError("Önceki çalışma alanı yedeği farklı; üzerine yazılmadı.")
        else:
            atomic_write_json(archive, original, mode=0o600)
        selection = None
    if selection is not None:
        validate_internal_path(Path(selection.workspace_root))
        return _selected_paths(Path(selection.workspace_root), selection.workspace_id)
    if initialize or (not identity_file().exists() and not os.environ.get("KSI_IDENTITY_FILE")):
        if not initialize:
            raise RuntimeError("KSI çalışma alanı henüz kurulmadı; uygulamayı açarak ilk kurulumu tamamlayın.")
        selection = new_internal_selection()
        root = Path(selection.workspace_root)
        validate_internal_path(root)
        if root.exists() and any(root.iterdir()):
            # Recovery after a crash between marker creation and selection
            # persistence must adopt the same identity, not overwrite data.
            from dataclasses import replace
            from ksi_local.workspace_selection import marker_identity

            selection = replace(selection, workspace_id=marker_identity(root))
        else:
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            atomic_write_json(safe_member(root, ".workspace-id"), {"workspace_id": selection.workspace_id})
        # Persist before model copying: an interrupted install resumes this same
        # workspace rather than generating a new identity over existing files.
        save_selection(selection)
        return _selected_paths(root, selection.workspace_id)
    raise RuntimeError("Eski depolama kaydı korunuyor; dahili kurulumu uygulamadan tamamlayın.")


def _selected_paths(root: Path, workspace_id: str) -> WorkspacePaths:
    validate_internal_path(root)
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
