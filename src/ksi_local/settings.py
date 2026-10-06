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
