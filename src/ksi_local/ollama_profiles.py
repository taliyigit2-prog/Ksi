"""Explicit text-only Qwen manifest compatibility; never modify model weights."""

import hashlib
import json
import re


def qwen_text_profile(original: bytes, expected_sha256: str) -> tuple[bytes, dict]:
    if not isinstance(original, bytes) or not 0 < len(original) <= 1024**2 or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256) or hashlib.sha256(original).hexdigest() != expected_sha256:
        raise ValueError("Qwen profile requires the exact pinned original registry manifest")
    data = json.loads(original)
    if not isinstance(data, dict) or data.get("schemaVersion") != 2:
        raise ValueError("Qwen registry manifest schema is unsupported")
    layers = data.get("layers")
    if not isinstance(layers, list) or not 1 <= len(layers) <= 64:
        raise ValueError("Qwen registry manifest layer inventory is invalid")
    for layer in [data.get("config"), *layers]:
        if not isinstance(layer, dict) or type(layer.get("size")) is not int or layer["size"] <= 0 or not isinstance(layer.get("digest"), str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", layer["digest"]):
            raise ValueError("Qwen registry manifest has an unpinned or malformed layer")
    projectors = [layer for layer in layers if layer.get("mediaType") == "application/vnd.ollama.image.projector"]
    models = [layer for layer in layers if layer.get("mediaType") == "application/vnd.ollama.image.model"]
    if len(projectors) != 1 or len(models) != 1:
        raise ValueError("Qwen compatibility profile requires exactly one model and split projector")
    derived = dict(data, layers=[layer for layer in layers if layer not in projectors])
    content = (json.dumps(derived, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    binding = dict(schema_version=1, generator="ksi_local.ollama_profiles.qwen_text_profile", generator_version=1,
        scope="Text-only summary compatibility with Ollama 0.24; no vision capability asserted",
        original_manifest_sha256=expected_sha256, derived_manifest_sha256=hashlib.sha256(content).hexdigest(),
        omitted_projector_reference=projectors[0]["digest"], original_projector_blob_required=True,
        model_blob_digest=models[0]["digest"], model_weights_modified=False)
    return content, binding
