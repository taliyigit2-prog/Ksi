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
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    inputs = json.loads((root / "config/runtime-sources.json").read_text(encoding="utf-8"))
    lock = json.loads((root / f"config/python-wheels-{args.architecture}.json").read_text(encoding="utf-8"))
    result = assemble_python_runtime(args.python_archive.absolute(), inputs["inputs"]["python-" + args.architecture], lock, args.wheelhouse.absolute(), args.destination.absolute())
    print(json.dumps({"architecture": result["architecture"], "packages": len(result["packages"]), "acceptance_tested": False}))


if __name__ == "__main__":
    main()
