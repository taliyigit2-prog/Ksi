#!/usr/bin/env python3
"""Collect official hash-pinned matching Qt/PySide source archives, no extraction."""

import argparse
import json
import runpy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ksi_local.atomic_files import atomic_write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[1]
    pins = json.loads((repository / "config/qt-corresponding-sources.json").read_text())
    if pins["schema_version"] != 1 or pins["version"] != "6.11.2":
        raise ValueError("Qt sources require the exact reviewed wheel version.")
    fetch = runpy.run_path(str(repository / "scripts/fetch_corresponding_source.py"))["fetch_source"]
    destination = args.destination.absolute()
    if destination.is_symlink():
        raise ValueError("Qt source cache cannot be linked.")
    def collect(item):
        name, digest = item
        filename = name + "-everywhere-src-" + pins["version"] + ".tar.xz"
        base = pins["pyside_base_url"] if name == "pyside-setup" else pins["qt_base_url"]
        target = fetch(base + filename, digest, destination / filename, limit=128 * 1024**2)
        atomic_write_json(target.with_name(filename + ".source.json"), {
            "schema_version": 1, "source_url": base + filename, "sha256": digest,
            "size": target.stat().st_size, "digest_source": pins["digest_source"],
            "redistribution_review_complete": False})
        print("Verified Qt source:", name, target.stat().st_size, flush=True)
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(collect, pins["sources"].items()))


if __name__ == "__main__":
    main()
