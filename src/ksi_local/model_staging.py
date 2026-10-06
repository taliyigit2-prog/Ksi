"""Build-only bounded extraction of exact pinned offline translation inputs."""

import json
import os
import stat
import zipfile
from pathlib import Path

from ksi_local.atomic_files import atomic_write_json
from ksi_local.bundle_runtime import digest_file, safe_member


ARGOS_INFERENCE_FILES = frozenset({"metadata.json", "README.md", "sentencepiece.model",
    "model/model.bin", "model/shared_vocabulary.txt"})


def stage_argos(archive: Path, destination: Path, specification: dict, *, pair: tuple[str, str]) -> dict:
    if archive.is_symlink() or not archive.is_file() or archive.stat().st_size != specification["size"] or digest_file(archive) != specification["sha256"]:
        raise ValueError("Argos source archive differs from its reviewed public digest.")
    if not destination.is_absolute() or destination.exists() or destination.is_symlink():
        raise FileExistsError("Argos staging requires a new explicit directory.")
    records = []
    with zipfile.ZipFile(archive) as source:
        members = source.infolist()
        if not 1 <= len(members) <= 1000 or sum(row.file_size for row in members) > 256 * 1024**2:
            raise ValueError("Argos archive exceeds the bounded expansion limit.")
        seen, roots = set(), set()
        for row in members:
            name = row.filename.rstrip("/")
            safe_member(destination, name)
            mode = row.external_attr >> 16
            if name.casefold() in seen or (stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}) or row.flag_bits & 1:
                raise ValueError("Argos archive has duplicate, encrypted or linked members.")
            seen.add(name.casefold())
            roots.add(name.split("/")[0])
        if len(roots) != 1:
            raise ValueError("Argos archive has multiple package roots.")
        root = next(iter(roots))
        selected = {row.filename.removeprefix(root + "/"): row for row in members
                    if not row.is_dir() and row.filename.startswith(root + "/")}
        if not ARGOS_INFERENCE_FILES <= selected.keys():
            raise ValueError("Argos inference files or upstream citations are missing.")
        metadata_entry = selected["metadata.json"]
        if metadata_entry.file_size > 64 * 1024:
            raise ValueError("Argos metadata exceeds its bound.")
        metadata = json.loads(source.read(metadata_entry))
        if not isinstance(metadata, dict) or (metadata.get("from_code"), metadata.get("to_code")) != pair:
            raise ValueError("Argos metadata does not match its pinned language direction.")
        destination.mkdir(parents=True, mode=0o700)
        for name in sorted(ARGOS_INFERENCE_FILES):
            member = selected[name]
            target = safe_member(destination, root + "/" + name)
            target.parent.mkdir(parents=True, exist_ok=True)
            received = 0
            with source.open(member) as content, target.open("xb") as output:
                while block := content.read(1024 * 1024):
                    received += len(block)
                    if received > member.file_size:
                        raise ValueError("Argos extracted member exceeds its archive size.")
                    output.write(block)
                output.flush()
                os.fsync(output.fileno())
            if received != member.file_size:
                raise ValueError("Argos extracted member is incomplete.")
            target.chmod(0o400)
            records.append({"path": target.relative_to(destination).as_posix(),
                "size": received, "sha256": digest_file(target)})
    result = {"schema_version": 1, "source_url": specification["url"],
        "source_sha256": specification["sha256"], "from_code": pair[0], "to_code": pair[1],
        "files": records, "unused_stanza_models_excluded": True,
        "acceptance_tested": False, "redistribution_review_complete": False}
    atomic_write_json(destination / "model-staging.json", result, mode=0o644)
    return result
