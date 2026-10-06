"""Preserve license texts and exact recipes from locked public native archives.

This is evidence collection, not an automatic license or corresponding-source
approval. Only archive metadata is extracted; package payloads are not copied.
"""

from __future__ import annotations

import io
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from ksi_local.atomic_files import atomic_write_bytes, atomic_write_json
from ksi_local.bundle_runtime import digest_file
from ksi_local.native_packages import validate_native_lock

MAX_METADATA_BYTES = 128 * 1024**2


def metadata_member(name: str) -> bool:
    path = PurePosixPath(name)
    if path.is_absolute() or "\\" in name or any(part in {"", ".", ".."} for part in name.split("/")):
        raise ValueError("Native archive metadata has an unsafe path.")
    return name.startswith(("info/licenses/", "info/recipe/")) or name in {"info/about.json", "info/index.json", "info/hash_input.json", "info/paths.json"}


def _info_tar(archive: Path, zstd: Path) -> bytes:
    with zipfile.ZipFile(archive) as bundle:
        members = [row for row in bundle.infolist() if row.filename.startswith("info-") and row.filename.endswith(".tar.zst")]
        if len(members) != 1 or members[0].file_size > MAX_METADATA_BYTES:
            raise ValueError("Native archive does not contain one bounded metadata stream.")
        compressed = bundle.read(members[0])
    with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as diagnostics:
        source.write(compressed)
        source.seek(0)
        process = subprocess.Popen([str(zstd), "--decompress", "--stdout", "--quiet"], stdin=source, stdout=subprocess.PIPE, stderr=diagnostics)
        try:
            result = process.stdout.read(MAX_METADATA_BYTES + 1)
            if len(result) > MAX_METADATA_BYTES:
                raise ValueError("Native archive metadata expansion exceeds its bound.")
            if process.wait(timeout=30) != 0:
                raise ValueError("Native archive metadata decompression failed.")
            return result
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdout.close()


def collect_native_notices(lock: dict, cache: Path, zstd: Path, destination: Path) -> dict:
    rows = validate_native_lock(lock)
    if destination.exists() or destination.is_symlink() or not destination.is_absolute():
        raise ValueError("Native notice destination must be new and absolute.")
    if zstd.is_symlink() or not zstd.is_file():
        raise ValueError("An explicit clean decompressor is required.")
    destination.mkdir(parents=True)
    records = []
    for row in rows:
        archive = cache / row["filename"]
        if archive.is_symlink() or not archive.is_file() or archive.stat().st_size != row["size"] or digest_file(archive) != row["sha256"]:
            raise ValueError("Native notice archive differs from its locked public input.")
        stream = io.BytesIO(_info_tar(archive, zstd)) if archive.suffix == ".conda" else None
        with tarfile.open(fileobj=stream, name=None if stream else str(archive), mode="r:*" if stream else "r|bz2") as source:
            files = []
            total = 0
            count = 0
            for member in source:
                count += 1
                if count > 100000:
                    raise ValueError("Native notice archive contains too many members.")
                selected = metadata_member(member.name)
                if not selected or member.isdir():
                    continue
                if not member.isfile() or not 0 <= member.size <= 16 * 1024**2:
                    raise ValueError("Native notice member is linked or oversized.")
                total += member.size
                if total > MAX_METADATA_BYTES:
                    raise ValueError("Native notices exceed the package bound.")
                content = source.extractfile(member)
                if content is None:
                    raise ValueError("Native notice member cannot be read.")
                with content:
                    data = content.read(member.size + 1)
                if len(data) != member.size:
                    raise ValueError("Native notice member is incomplete.")
                target = destination / row["name"] / member.name
                if target.exists():
                    raise ValueError("Native notice archive has duplicate members.")
                target.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_bytes(target, data, mode=0o644)
                files.append({"path": target.relative_to(destination).as_posix(), "sha256": digest_file(target), "size": member.size})
        records.append({"name": row["name"], "version": row["version"], "license_declared": row["license"], "archive_url": row["url"], "archive_sha256": row["sha256"], "files": files, "license_text_present": any("/info/licenses/" in item["path"] for item in files)})
    result = {"schema_version": 1, "architecture": lock["architecture"], "packages": records, "redistribution_review_complete": False, "corresponding_sources_complete": False}
    atomic_write_json(destination / "native-notices.json", result)
    return result
