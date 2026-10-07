#!/usr/bin/env python3
"""Safely stage a native main or isolated speech prefix from public CI."""

import argparse
import hashlib
import json
import subprocess
import tarfile
from pathlib import Path, PurePosixPath

from ksi_local.app_assembly import copy_clean_tree
from ksi_local.bundle_runtime import digest_file, safe_member


ROOTS = {"runtime-piper", "piper-source", "espeak-source"}
MACHO_MAGICS = {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xfe\xed\xfa\xce", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("architecture", choices=["arm64", "x86_64"])
    parser.add_argument("archive", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--scope", choices=("piper", "main"), default="piper")
    args = parser.parse_args()
    destination = args.destination.absolute()
    runtime_name = "runtime-" + args.scope
    roots = ROOTS if args.scope == "piper" else {runtime_name}
    if args.archive.is_symlink() or not args.archive.is_file() or destination.exists() or destination.is_symlink():
        raise ValueError("Speech import requires a normal archive and new explicit staging.")
    with tarfile.open(args.archive, "r:gz") as stream:
        members = stream.getmembers()
        expansion_limit = (2 if args.scope == "piper" else 4) * 1024**3
        if not 1 <= len(members) <= 100000 or sum(row.size for row in members) > expansion_limit:
            raise ValueError("Speech artifact exceeds safe expansion bounds.")
        seen = set()
        for row in members:
            name = row.name.rstrip("/")
            safe_member(destination, name)
            root = name.split("/")[0]
            if root not in roots or name.casefold() in seen or not (row.isfile() or row.isdir() or row.issym() or row.islnk()):
                raise ValueError("Speech artifact has an unexpected or duplicate member.")
            seen.add(name.casefold())
            if row.issym() or row.islnk():
                target = PurePosixPath(row.linkname)
                if root != runtime_name or target.is_absolute() or "\\" in row.linkname:
                    raise ValueError("Speech artifact link is outside its runtime.")
                combined = PurePosixPath(name).parent / target if row.issym() else target
                parts = []
                for part in combined.parts:
                    if part == "..":
                        if not parts:
                            raise ValueError("Speech link escapes its runtime.")
                        parts.pop()
                    elif part != ".":
                        parts.append(part)
                if not parts or parts[0] != runtime_name:
                    raise ValueError("Speech link crosses the runtime boundary.")
        destination.mkdir(parents=True, mode=0o700)
        stream.extractall(destination, members=members, filter="data")
    repository = Path(__file__).resolve().parents[1]
    suffix = "" if args.scope == "main" else "-piper"
    lock = json.loads((repository / f"config/python{suffix}-wheels-{args.architecture}.json").read_text())
    inputs = json.loads((repository / "config/runtime-sources.json").read_text())["inputs"]
    runtime = destination / runtime_name
    provenance = json.loads((runtime / "runtime-provenance.json").read_text())
    expected = hashlib.sha256(json.dumps(lock, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if provenance.get("architecture") != args.architecture or provenance.get("wheel_lock_sha256") != expected or provenance.get("python_source_sha256") != inputs["python-" + args.architecture]["sha256"]:
        raise ValueError("Speech artifact provenance differs from reviewed public inputs.")
    materialized = destination / "runtime-materialized"
    copy_clean_tree(runtime, materialized)
    native_count = 0
    for path in materialized.rglob("*"):
        if not path.is_file():
            continue
        with path.open("rb") as content:
            magic = content.read(4)
        if magic in MACHO_MAGICS:
            architectures = subprocess.run(["/usr/bin/lipo", "-archs", str(path)], check=True, capture_output=True, text=True, timeout=30).stdout.split()
            if args.architecture not in architectures:
                raise ValueError("Speech binary is not compatible with its claimed native architecture.")
            native_count += 1
    if native_count < 1:
        raise ValueError("Speech artifact has no actual native binaries.")
    print(json.dumps({"architecture": args.architecture, "scope": args.scope, "native_files": native_count,
        "archive_sha256": digest_file(args.archive), "acceptance_tested": False}))


if __name__ == "__main__":
    main()
