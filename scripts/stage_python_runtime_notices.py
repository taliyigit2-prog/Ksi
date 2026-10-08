#!/usr/bin/env python3
"""Restore pinned original Python legal texts during a clean build only."""

import argparse
import json
import tempfile
from pathlib import Path

from ksi_local.build_inputs import fetch_pinned_input
from ksi_local.python_runtime_notices import stage_python_runtime_notices
from ksi_local.bundle_runtime import MAX_MANIFEST_BYTES, host_architecture, safe_member
from ksi_local.zstd_build_input import decompress_build_archive


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("architecture", choices=("arm64", "x86_64"))
    parser.add_argument("cache", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--verified-components", type=Path,
        help="Explicit reviewed native component root supplying the pinned Zstandard decoder")
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
    if args.verified_components is None:
        result = stage_python_runtime_notices(archive, pin, args.destination.absolute())
    else:
        components = args.verified_components.absolute()
        specification = safe_member(components, "component-specification.json")
        if components.is_symlink() or not components.is_dir() or specification.is_symlink() or not specification.is_file() or specification.stat().st_size > MAX_MANIFEST_BYTES:
            raise ValueError("Decoder inputs need an explicit bounded reviewed component root.")
        document = json.loads(specification.read_bytes())
        if document["architecture"] != host_architecture():
            raise ValueError("The original archive decoder must match this actual native processor.")
        libraries = [row for row in document["files"] if row["path"] == "engines/media/lib/libzstd.1.dylib"]
        if len(libraries) != 1:
            raise ValueError("No unique exact native Zstandard decoder is present.")
        library = libraries[0]
        with tempfile.TemporaryFile(dir=archive.parent) as original_tar:
            decompress_build_archive(archive, safe_member(components, library["path"]), library["sha256"], original_tar)
            result = stage_python_runtime_notices(archive, pin, args.destination.absolute(), original_tar=original_tar)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
