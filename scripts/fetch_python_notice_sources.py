#!/usr/bin/env python3
"""Fetch pinned missing Python license texts and original source distributions."""

import argparse
import json
from pathlib import Path

from ksi_local.atomic_files import atomic_write_json
from ksi_local.build_inputs import fetch_pinned_input


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[1]
    inputs = json.loads((repository / "config/python-notice-sources.json").read_text())["inputs"]
    root = args.destination.absolute()
    if root.is_symlink():
        raise ValueError("Python license source cache cannot be linked.")
    for identifier, entry in inputs.items():
        target = root / (identifier + (".txt" if entry["type"] == "license" else ".tar.gz"))
        verified = fetch_pinned_input(entry, target)
        atomic_write_json(target.with_name(target.name + ".source.json"), dict(entry, verified=True,
            redistribution_review_complete=False))
        print("Verified Python license input:", identifier, verified.stat().st_size, flush=True)


if __name__ == "__main__":
    main()
