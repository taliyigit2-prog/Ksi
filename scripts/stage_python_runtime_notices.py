#!/usr/bin/env python3
"""Restore pinned original Python legal texts during a clean build only."""

import argparse
import json
from pathlib import Path

from ksi_local.build_inputs import fetch_pinned_input
from ksi_local.python_runtime_notices import stage_python_runtime_notices


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("architecture", choices=("arm64", "x86_64"))
    parser.add_argument("cache", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--allow-network", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    pin = json.loads((root / "config/python-runtime-notices.json").read_bytes())["inputs"][args.architecture]
    runtime = json.loads((root / "config/runtime-sources.json").read_bytes())["inputs"]["python-" + args.architecture]
    if pin["install_only_archive"] != runtime:
        raise ValueError("Original notices do not bind this exact public runtime input.")
    archive = args.cache.absolute() / ("python-full-original-" + args.architecture + ".tar.zst")
    if not archive.exists() and not args.allow_network:
        raise FileNotFoundError("Original full archive must be supplied offline or explicitly fetched for this build.")
    if args.allow_network:
        fetch_pinned_input(pin["full_archive"], archive)
    print(json.dumps(stage_python_runtime_notices(archive, pin, args.destination.absolute())))


if __name__ == "__main__":
    main()
