#!/usr/bin/env python3
"""Install a fresh native, hash-locked runtime, without touching personal data."""

import argparse
import json
from pathlib import Path

from ksi_local.clean_runtime import assemble_python_runtime


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("architecture", choices=["arm64", "x86_64"])
    parser.add_argument("python_archive", type=Path)
    parser.add_argument("wheelhouse", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--build-tools", action="store_true")
    parser.add_argument("--lock", type=Path, help="Explicit separately reviewed engine wheel lock")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    inputs = json.loads((root / "config/runtime-sources.json").read_text(encoding="utf-8"))
    kind = "python-build-wheels" if args.build_tools else "python-wheels"
    if args.lock and args.build_tools:
        parser.error("Choose either build tools or an explicit engine lock.")
    lock_path = args.lock or root / f"config/{kind}-{args.architecture}.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    if lock.get("architecture") != args.architecture:
        raise ValueError("Explicit engine wheel lock does not match the requested architecture.")
    result = assemble_python_runtime(args.python_archive.absolute(), inputs["inputs"]["python-" + args.architecture], lock, args.wheelhouse.absolute(), args.destination.absolute())
    print(json.dumps({"architecture": result["architecture"], "packages": len(result["packages"]), "acceptance_tested": False}))


if __name__ == "__main__":
    main()
