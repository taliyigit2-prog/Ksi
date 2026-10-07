"""Verify installed model files before an engine can deserialize them."""

import json
import re
from pathlib import Path

from ksi_local.bundle_runtime import OfflinePayload, digest_file, safe_member


def verify_model_tree(directory: Path, resources: Path, *, prefix: str, required: frozenset[str] = frozenset()) -> OfflinePayload:
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("Yerel model klasörü geçersiz.")
    payload = OfflinePayload.load(resources)
    entries = [entry for entry in payload.files if entry.role == "model" and entry.path.startswith(prefix)]
    if not entries or not required <= {entry.path.removeprefix(prefix) for entry in entries}:
        raise RuntimeError("Yerel model paketinin gerekli dosyaları eksik.")
    for entry in entries:
        installed = safe_member(directory, entry.path.removeprefix(prefix))
        if not installed.is_file() or installed.stat().st_size != entry.size or digest_file(installed) != entry.sha256:
            raise RuntimeError("Yerel model dosyası bütünlük doğrulamasından geçmedi.")
    return payload


OLLAMA_MANIFESTS = frozenset({
    "manifests/registry.ollama.ai/library/translategemma/4b-it-q8_0",
    "manifests/registry.ollama.ai/library/qwen3.5/4b",
})


def verify_ollama_store(directory: Path, resources: Path) -> None:
    prefix = "models/ollama/"
    payload = verify_model_tree(directory, resources, prefix=prefix, required=OLLAMA_MANIFESTS)
    entries = {entry.path.removeprefix(prefix): entry for entry in payload.files
               if entry.role == "model" and entry.path.startswith(prefix)}
    for name in OLLAMA_MANIFESTS:
        entry = entries[name]
        if entry.size > 1024**2:
            raise ValueError("Ollama manifesti boyut sınırını aşıyor.")
        manifest = json.loads(safe_member(directory, name).read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or manifest.get("schemaVersion") != 2:
            raise ValueError("Ollama manifesti sürümü geçersiz.")
        layers = manifest.get("layers")
        if not isinstance(layers, list) or not 1 <= len(layers) <= 64:
            raise ValueError("Ollama manifestinin katman listesi geçersiz.")
        for layer in [manifest.get("config"), *layers]:
            if not isinstance(layer, dict) or not isinstance(layer.get("digest"), str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", layer["digest"]):
                raise ValueError("Ollama katmanı sabit SHA-256 kaydı içermiyor.")
            blob = entries.get("blobs/" + layer["digest"].replace(":", "-"))
            if blob is None or type(layer.get("size")) is not int or blob.size != layer["size"] or blob.sha256 != layer["digest"].removeprefix("sha256:"):
                raise RuntimeError("Ollama manifesti doğrulanmamış veya eksik bir katmana başvuruyor.")
