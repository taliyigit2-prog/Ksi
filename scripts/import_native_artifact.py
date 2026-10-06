#!/usr/bin/env python3
"""Import a narrow public native CI artifact into new, verified build staging."""

import argparse
import json
import subprocess
import tarfile
from pathlib import Path

from ksi_local.bundle_runtime import digest_file, safe_member


ROOTS = {"native-artifact", "whisper-source", "native-relocated", "native-notices", "ffmpeg-source", "imagemagick-source"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("architecture", choices=["arm64", "x86_64"])
    parser.add_argument("archive", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    destination = args.destination.absolute()
    if args.archive.is_symlink() or not args.archive.is_file() or destination.exists() or destination.is_symlink():
        raise ValueError("Native artifact requires an explicit normal archive and new staging.")
    with tarfile.open(args.archive, "r:gz") as stream:
        members = stream.getmembers()
        if not 1 <= len(members) <= 100000 or sum(row.size for row in members) > 2 * 1024**3:
            raise ValueError("Native artifact exceeds safe expansion bounds.")
        seen = set()
        for row in members:
            name = row.name.rstrip("/")
            safe_member(destination, name)
            if name.split("/")[0] not in ROOTS or name.casefold() in seen or not (row.isfile() or row.isdir()):
                raise ValueError("Native artifact contains an unexpected, linked or duplicate member.")
            seen.add(name.casefold())
        destination.mkdir(parents=True, mode=0o700)
        stream.extractall(destination, members=members, filter="data")
    native = destination / "native-relocated"
    record = json.loads((native / "native-staging.json").read_text())
    expected = set()
    for row in record["files"]:
        path = safe_member(native, row["path"])
        if not path.is_file() or path.stat().st_size != row["size"] or digest_file(path) != row["staged_sha256"]:
            raise ValueError("Imported engine differs from its native staged inventory.")
        architectures = subprocess.run(["/usr/bin/lipo", "-archs", str(path)], check=True, capture_output=True, text=True, timeout=30).stdout.split()
        if architectures != [args.architecture]:
            raise ValueError("Imported engine is not the claimed actual native architecture.")
        subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(path)], check=True, capture_output=True, timeout=30)
        expected.add(row["path"])
    actual = {path.relative_to(native).as_posix() for path in native.rglob("*") if path.is_file() and path.name != "native-staging.json"}
    if actual != expected:
        raise ValueError("Imported engine graph contains unrecorded files.")
    whisper = destination / "native-artifact/whisper-cli"
    architectures = subprocess.run(["/usr/bin/lipo", "-archs", str(whisper)], check=True, capture_output=True, text=True, timeout=30).stdout.split()
    if architectures != [args.architecture]:
        raise ValueError("Imported CPU speech engine architecture does not match.")
    print(json.dumps({"architecture": args.architecture, "native_files": len(expected), "archive_sha256": digest_file(args.archive), "acceptance_tested": False, "redistribution_review_complete": False}))


if __name__ == "__main__":
    main()
