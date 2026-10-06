#!/usr/bin/env python3
"""Select exact locked wheels into a new cache without downloading or hardlinks."""

import argparse
import json
import shutil
from pathlib import Path

from ksi_local.bundle_runtime import digest_file
from ksi_local.copy_on_write import clone_file
from ksi_local.wheel_lock import validate_wheel_lock


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("lock", type=Path)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    if args.destination.exists() or args.destination.is_symlink() or args.source.is_symlink() or not args.source.is_dir():
        raise ValueError("Wheel selection requires a normal cache and new destination.")
    rows = validate_wheel_lock(json.loads(args.lock.read_text()))
    for row in rows:
        source = args.source / row["filename"]
        if source.is_symlink() or not source.is_file() or source.stat().st_size != row["size"] or digest_file(source) != row["sha256"]:
            raise ValueError("Selected wheel differs from its official public lock.")
    args.destination.mkdir(parents=True, mode=0o700)
    for row in rows:
        source = args.source / row["filename"]
        target = args.destination / row["filename"]
        if not clone_file(source, target):
            shutil.copyfile(source, target)
        target.chmod(0o400)
    print(f"Selected {len(rows)} exact locked wheels without network access.")


if __name__ == "__main__":
    main()
