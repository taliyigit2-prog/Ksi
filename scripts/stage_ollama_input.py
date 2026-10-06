#!/usr/bin/env python3
"""Stage exact public Ollama CLI/runtime members for one native architecture."""

import argparse
import json
import subprocess
import tarfile
from pathlib import Path, PurePosixPath

from ksi_local.app_assembly import copy_clean_tree
from ksi_local.atomic_files import atomic_write_json
from ksi_local.bundle_runtime import digest_file, safe_member
from ksi_local.copy_on_write import clone_file


MAGICS = {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xfe\xed\xfa\xce", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("architecture", choices=["arm64", "x86_64"])
    parser.add_argument("archive", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[1]
    entry = json.loads((repository / "config/native-sources.json").read_text())["inputs"]["ollama-macos"]
    if args.archive.is_symlink() or not args.archive.is_file() or args.archive.stat().st_size != entry["size"] or digest_file(args.archive) != entry["sha256"]:
        raise ValueError("Ollama archive differs from its official pinned release digest.")
    destination = args.destination.absolute()
    raw = destination.with_name(destination.name + "-raw")
    materialized = destination.with_name(destination.name + "-materialized")
    if any(path.exists() or path.is_symlink() for path in (destination, raw, materialized)):
        raise FileExistsError("Ollama staging requires separate new directories.")
    with tarfile.open(args.archive, "r:gz") as source:
        members = source.getmembers()
        if not 1 <= len(members) <= 1000 or sum(row.size for row in members) > 1024**3:
            raise ValueError("Ollama archive exceeds the bounded expansion limit.")
        seen = set()
        for row in members:
            name = row.name.rstrip("/")
            safe_member(raw, name)
            if name.casefold() in seen or not (row.isfile() or row.isdir() or row.issym()):
                raise ValueError("Ollama archive has unexpected or duplicate members.")
            seen.add(name.casefold())
            if row.issym():
                target = PurePosixPath(row.linkname)
                if target.is_absolute() or ".." in target.parts or "\\" in row.linkname:
                    raise ValueError("Ollama archive has an unsafe linked member.")
        raw.mkdir(parents=True, mode=0o700)
        source.extractall(raw, members=members, filter="data")
    copy_clean_tree(raw, materialized)
    destination.mkdir(parents=True, mode=0o700)
    records, excluded = [], []
    for origin in sorted(materialized.rglob("*")):
        if not origin.is_file():
            continue
        name = origin.relative_to(materialized).as_posix()
        if origin.name.startswith("._") or (args.architecture == "x86_64" and name.startswith("mlx_metal_")):
            excluded.append(name)
            continue
        with origin.open("rb") as content:
            native = content.read(4) in MAGICS
        if native:
            architectures = subprocess.run(["/usr/bin/lipo", "-archs", str(origin)], check=True, capture_output=True, text=True, timeout=30).stdout.split()
            if args.architecture not in architectures:
                excluded.append(name)
                continue
        target = safe_member(destination, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not clone_file(origin, target):
            import shutil
            shutil.copy2(origin, target)
        target.chmod(0o755 if native else 0o644)
        original_hash = digest_file(origin)
        if native:
            subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", str(target)], check=True, capture_output=True, timeout=60)
            subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(target)], check=True, capture_output=True, timeout=30)
        records.append({"path": name, "original_sha256": original_hash,
            "staged_sha256": digest_file(target), "size": target.stat().st_size, "native": native})
    if not (destination / "ollama").is_file():
        raise ValueError("Ollama release has no compatible CLI for the target.")
    record = {"schema_version": 1, "architecture": args.architecture,
        "source_url": entry["url"], "archive_sha256": entry["sha256"],
        "version": entry["version"], "files": records, "excluded_members": excluded,
        "signing": "adhoc", "acceptance_tested": False, "redistribution_review_complete": False}
    atomic_write_json(destination / "tool-staging.json", record, mode=0o644)
    print(json.dumps({"architecture": args.architecture, "staged_files": len(records),
        "excluded_files": len(excluded), "acceptance_tested": False}))


if __name__ == "__main__":
    main()
