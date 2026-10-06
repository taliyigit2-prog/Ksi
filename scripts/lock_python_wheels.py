#!/usr/bin/env python3
"""Generate a reviewed public lock or fetch its exact wheels for a clean build."""

import argparse
import json
from pathlib import Path

from ksi_local.atomic_files import atomic_write_json, atomic_write_text
from ksi_local.wheel_lock import fetch_wheelhouse, lock_from_reports, requirements_text


def main():
    parser = argparse.ArgumentParser()
    actions = parser.add_subparsers(dest="action", required=True)
    create = actions.add_parser("create")
    create.add_argument("architecture", choices=["arm64", "x86_64"])
    create.add_argument("destination", type=Path)
    create.add_argument("reports", type=Path, nargs="+")
    fetch = actions.add_parser("fetch")
    fetch.add_argument("lock", type=Path)
    fetch.add_argument("directory", type=Path)
    fetch.add_argument("--requirements", type=Path)
    args = parser.parse_args()
    if args.action == "create":
        if args.destination.exists():
            raise FileExistsError("An existing public wheel lock is not replaced.")
        data = lock_from_reports(args.reports, architecture=args.architecture)
        atomic_write_json(args.destination, data, mode=0o644)
        print(f'Locked {len(data["wheels"])} official wheels for {args.architecture}.')
    else:
        data = json.loads(args.lock.read_text(encoding="utf-8"))
        fetch_wheelhouse(data, args.directory, on_progress=lambda done, total: print(f"Verified wheels: {done}/{total}", flush=True))
        if args.requirements:
            atomic_write_text(args.requirements, requirements_text(data))


if __name__ == "__main__":
    main()
