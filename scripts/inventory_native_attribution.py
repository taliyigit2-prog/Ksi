#!/usr/bin/env python3
"""Produce package ownership evidence for the actual signed native graph."""

import argparse
import json
from pathlib import Path

from ksi_local.atomic_files import atomic_write_json
from ksi_local.native_attribution import inventory_native_attribution


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("lock", type=Path)
    parser.add_argument("notices", type=Path)
    parser.add_argument("graph", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    if args.destination.exists() or args.destination.is_symlink():
        raise FileExistsError("Native attribution evidence is never overwritten.")
    if args.lock.is_symlink() or args.lock.stat().st_size > 8 * 1024**2:
        raise ValueError("Native package lock is linked or oversized.")
    result = inventory_native_attribution(json.loads(args.lock.read_text()), args.notices.absolute(),
        args.graph.absolute(), notice_fallbacks={"libfreetype": "freetype", "libfreetype6": "freetype"})
    atomic_write_json(args.destination.absolute(), result, mode=0o644)
    print(json.dumps({"architecture": result["architecture"], "libraries": len(result["libraries"]),
        "missing_license_texts": result["missing_license_texts"], "redistribution_review_complete": False}))


if __name__ == "__main__":
    main()
