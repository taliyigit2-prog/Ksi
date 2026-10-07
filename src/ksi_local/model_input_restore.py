"""Explicit build-only restoration from committed official model input pins."""
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from ksi_local.acceptance_inputs import deferred_models
from ksi_local.build_inputs import fetch_pinned_input
from ksi_local.bundle_runtime import digest_file, safe_member
from ksi_local.internal_storage import validate_internal_path


def restore_model_inputs(root: Path, model_sources: dict, ollama_sources: dict, *, allow_network=False) -> dict:
    if not allow_network:
        raise ValueError("Official build-input downloads require explicit network authorization")
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise ValueError("Restoration requires a normal imported internal build directory")
    validate_internal_path(root)
    specification = safe_member(root, "components/component-specification.json")
    if not specification.is_file() or specification.stat().st_size > 32 * 1024**2:
        raise ValueError("Imported component specification is missing or oversized")
    components = json.loads(specification.read_bytes())
    deferred = deferred_models(root, components)
    pins = {}
    for identifier, pin in model_sources["inputs"].items():
        pins[identifier] = pin
    for model in ollama_sources["models"]:
        name = model["name"]
        if not re.fullmatch(r"[a-z0-9._-]+", name):
            raise ValueError("Committed model name is invalid")
        for layer in model["layers"]:
            digest = layer["sha256"]
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("Committed model digest is invalid")
            pins[f"{name}-{layer['role']}"] = dict(size=layer["size"], sha256=digest,
                url=f"https://registry.ollama.ai/v2/library/{name}/blobs/sha256:{digest}")
    plan = []
    for row in deferred:
        pin = pins.get(row["identifier"])
        if pin is None or pin.get("size") != row["size"] or pin.get("sha256") != row["sha256"]:
            raise ValueError("Deferred model is not an exact committed original source pin")
        address = urlsplit(pin["url"])
        if (address.scheme != "https" or address.hostname not in {"registry.ollama.ai", "huggingface.co"}
                or address.username or address.password or address.query or address.fragment):
            raise ValueError("Deferred model must use a clean official public source")
        target = safe_member(root / "components", row["path"])
        plan.append((pin, target))
    for pin, target in plan:
        fetch_pinned_input(pin, target)
    for pin, target in plan:
        if target.stat().st_size != pin["size"] or digest_file(target) != pin["sha256"]:
            raise ValueError("Restored model bytes differ from the committed original pin")
    return dict(restored_model_files=len(plan), native_acceptance_performed=False,
                scope="Explicit network-enabled build stage; final app must independently seal all model files")
