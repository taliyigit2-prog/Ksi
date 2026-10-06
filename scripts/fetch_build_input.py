#!/usr/bin/env python3
"""Download a named public lock entry into a private build cache."""

import argparse
import json
from pathlib import Path

from ksi_local.build_inputs import fetch_pinned_input, extract_python_input


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("identifier")
    parser.add_argument("destination", type=Path)
    parser.add_argument("--extract-to", type=Path)
    parser.add_argument("--catalog", choices=["runtime", "native"], default="runtime")
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1] / f"config/{args.catalog}-sources.json"
    data = json.loads(source.read_text(encoding="utf-8"))
    entry = data["inputs"][args.identifier]
    path = fetch_pinned_input(entry, args.destination.absolute())
    if args.extract_to:
        if args.catalog != "runtime" or not args.identifier.startswith("python-"):
            raise ValueError("The Python extractor accepts only a pinned Python runtime input.")
        extract_python_input(path, args.extract_to.absolute(), sha256=entry["sha256"])
    print(json.dumps({"identifier": args.identifier, "verified": True, "bytes": path.stat().st_size}))


if __name__ == "__main__":
    main()
