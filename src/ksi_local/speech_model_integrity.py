"""Verify installed speech weights before deserialization or checkpoint reuse."""

import hashlib
from pathlib import Path

from ksi_local.bundle_runtime import OfflinePayload, digest_file, safe_member


CHATTERBOX_FILES = frozenset({
    "t3_mtl23ls_v3.safetensors", "s3gen.pt", "ve.pt", "conds.pt",
    "grapheme_mtl_merged_expanded_v1.json",
})


def verify_chatterbox(directory: Path, resources: Path) -> str:
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("Türkçe ses modeli klasörü geçersiz.")
    payload = OfflinePayload.load(resources)
    matches = {}
    for entry in payload.files:
        if entry.role != "model" or Path(entry.path).name not in CHATTERBOX_FILES:
            continue
        name = Path(entry.path).name
        if name in matches:
            raise ValueError("Türkçe ses modeli paketinde dosya çakışması var.")
        matches[name] = entry
    if set(matches) != CHATTERBOX_FILES:
        raise RuntimeError("Türkçe ses modeli paketinin gerekli dosyaları eksik.")
    identity = hashlib.sha256()
    for name in sorted(matches):
        entry = matches[name]
        installed = safe_member(directory, name)
        if not installed.is_file() or installed.stat().st_size != entry.size or digest_file(installed) != entry.sha256:
            raise RuntimeError("Türkçe ses modeli bütünlük doğrulamasından geçmedi.")
        identity.update(f"{name}:{entry.sha256}\n".encode("ascii"))
    return identity.hexdigest()
