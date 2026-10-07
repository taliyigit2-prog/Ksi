"""Merge explicit pinned public component stages without ambient discovery."""

import json
import os
import shutil
from pathlib import Path

from ksi_local.atomic_files import atomic_write_json
from ksi_local.bundle_runtime import digest_file, safe_member
from ksi_local.copy_on_write import clone_file


def merge_components(pieces: tuple[Path, ...], destination: Path, *, architecture: str) -> dict:
    if architecture not in {"arm64", "x86_64"} or not pieces or len(pieces) > 32:
        raise ValueError("Explicit component pieces/architecture are invalid.")
    if not destination.is_absolute() or destination.exists() or destination.is_symlink():
        raise FileExistsError("Merged staging requires a new explicit directory.")
    by_path, by_id, models, model_ids, plan = {}, {}, [], set(), []
    for piece in pieces:
        if piece.is_symlink() or not piece.is_dir():
            raise ValueError("Component stage must be a normal explicit directory.")
        manifest = safe_member(piece, "component-specification.json")
        if not manifest.is_file() or manifest.stat().st_size > 16 * 1024**2:
            raise ValueError("Component specification is missing or oversized.")
        spec = json.loads(manifest.read_text(encoding="utf-8"))
        if spec.get("schema_version") != 1 or spec.get("architecture") != architecture:
            raise ValueError("Component stage uses another architecture/version.")
        entries = spec.get("files")
        if not isinstance(entries, list) or not entries or len(entries) > 50000:
            raise ValueError("Component stage inventory is invalid.")
        for row in entries:
            relative = row["path"]
            if relative.startswith("runtime/") or relative == "component-specification.json":
                raise ValueError("Component cannot replace main runtime/source/specification.")
            origin = safe_member(piece, relative)
            safe_member(destination, relative)
            if not origin.is_file() or origin.stat().st_size != row["size"] or digest_file(origin) != row["sha256"]:
                raise ValueError("Pinned component changed before merge.")
            path_key, id_key = relative.casefold(), (row["role"], row["identifier"])
            previous = by_path.get(path_key)
            if previous is not None:
                if previous != row:
                    raise ValueError("Different component records collide at a path.")
                continue  # Identical original notice/source only needs one copy.
            if id_key in by_id:
                raise ValueError("Component identifiers collide across stages.")
            by_path[path_key] = row
            by_id[id_key] = relative
            plan.append((origin, relative))
            if len(by_path) > 50000:
                raise ValueError("Merged component inventory exceeds its bound.")
        for model in spec.get("models", []):
            if model["id"] in model_ids:
                raise ValueError("Model family occurs in more than one component stage.")
            model_ids.add(model["id"])
            models.append(model)
    destination.mkdir(parents=True, mode=0o700)
    for origin, relative in plan:
        target = safe_member(destination, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not clone_file(origin, target):
            shutil.copy2(origin, target)
        target.chmod(0o755 if os.access(origin, os.X_OK) else 0o644)
    specification = {"schema_version": 1, "architecture": architecture,
        "files": sorted(by_path.values(), key=lambda row: row["path"]), "models": models}
    atomic_write_json(destination / "component-specification.json", specification, mode=0o644)
    return {"architecture": architecture, "files": len(by_path), "models": len(models),
        "payload_bytes": sum(row["size"] for row in by_path.values()), "acceptance_tested": False}
