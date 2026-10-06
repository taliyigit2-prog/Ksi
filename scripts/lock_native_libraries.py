#!/usr/bin/env python3
"""Lock, fetch or install only the selected clean native build libraries."""

import argparse
import json
from pathlib import Path

from ksi_local.atomic_files import atomic_write_json
from ksi_local.native_packages import fetch_native_libraries, install_native_prefix, native_lock_from_report, sign_native_prefix


def main():
    parser = argparse.ArgumentParser()
    actions = parser.add_subparsers(dest="action", required=True)
    create = actions.add_parser("create")
    create.add_argument("architecture", choices=["arm64", "x86_64"])
    create.add_argument("report", type=Path)
    create.add_argument("destination", type=Path)
    sign = actions.add_parser("sign-prefix")
    sign.add_argument("prefix", type=Path)
    for name in ("fetch", "install"):
        action = actions.add_parser(name)
        action.add_argument("lock", type=Path)
        action.add_argument("cache", type=Path)
        if name == "install":
            action.add_argument("micromamba", type=Path)
            action.add_argument("destination", type=Path)
    args = parser.parse_args()
    if args.action == "sign-prefix":
        root = Path(__file__).resolve().parents[1]
        prefix = args.prefix.absolute()
        if prefix not in {root / "build/native-prefix-arm64", root / "build/native-prefix-x86_64"}:
            raise ValueError("Signing is restricted to this builder's explicit generated native prefixes.")
        print(json.dumps({"signed_files": len(sign_native_prefix(prefix)["files"]), "acceptance_tested": False}))
    elif args.action == "create":
        if args.destination.exists():
            raise FileExistsError("An existing native library lock is never replaced.")
        data = native_lock_from_report(args.report, args.architecture)
        atomic_write_json(args.destination, data, mode=0o644)
        print(f"Locked {len(data['packages'])} native libraries; redistribution review pending.")
    else:
        data = json.loads(args.lock.read_text(encoding="utf-8"))
        if args.action == "fetch":
            fetch_native_libraries(data, args.cache)
        else:
            root = Path(__file__).resolve().parents[1]
            inputs = json.loads((root / "config/native-sources.json").read_text(encoding="utf-8"))["inputs"]
            expected = inputs["micromamba-" + data["architecture"]]["sha256"]
            install_native_prefix(data, args.cache.absolute(), args.micromamba.absolute(), expected, args.destination.absolute())


if __name__ == "__main__":
    main()
