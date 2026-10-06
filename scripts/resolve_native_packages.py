#!/usr/bin/env python3
"""Build-only dry-run of public native packages with isolated configuration.

The full report stays in the ignored build directory, not public source.
This does not install packages, establish redistribution rights or ship tools.
"""

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

from ksi_local.atomic_files import atomic_write_json
from ksi_local.bundle_runtime import digest_file, host_architecture


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("micromamba", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--architecture", choices=["arm64", "x86_64"], default=host_architecture())
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    executable = args.micromamba.absolute()
    inputs = json.loads((root / "config/native-sources.json").read_text(encoding="utf-8"))["inputs"]
    expected = inputs["micromamba-" + host_architecture()]
    if executable.is_symlink() or not executable.is_file() or digest_file(executable) != expected["sha256"]:
        raise ValueError("Native resolver does not match the pinned official build tool.")
    destination = args.destination.absolute()
    if destination.exists() or not destination.is_relative_to((root / "build").resolve()):
        raise ValueError("Native solver output must be a new private build report.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".ksi-native-solver-", dir=destination.parent) as temporary:
        environment = {"HOME": temporary, "PATH": "/usr/bin:/bin", "MAMBA_ROOT_PREFIX": str(root / "build/mamba-root"), "CONDA_OVERRIDE_OSX": "14.0", "CONDA_OVERRIDE_ARCHSPEC": "arm64" if args.architecture == "arm64" else "x86_64"}
        # Build FFmpeg/ImageMagick ourselves with only selected codec delegates.
        # Full binary distributions pull unrelated Ghostscript/GUI/OpenVINO
        # surfaces that the constrained KSI adapters do not require.
        dependencies = ["x264", "libvpx", "libopus", "libass", "libharfbuzz-devel", "expat", "liblzma-devel", "zlib", "lame", "libheif", "libwebp-base", "libjpeg-turbo", "libpng", "libtiff", "freetype", "pkg-config"]
        result = subprocess.run([str(executable), "--no-rc", "create", "--prefix", str(Path(temporary) / "target"), "--dry-run", "--json", "--yes", "--override-channels", "--channel", "conda-forge", "--platform", "osx-arm64" if args.architecture == "arm64" else "osx-64", *dependencies], env=environment, check=False, capture_output=True, timeout=600)
    if len(result.stdout) > 16 * 1024**2:
        raise ValueError("Native solver report exceeds the build limit.")
    data = json.loads(result.stdout)
    atomic_write_json(destination, data)
    if result.returncode or not data.get("success"):
        raise ValueError("Native dependency solver did not succeed: " + str(data.get("solver_problems", data.get("error", "see private build report")))[:1500])
    print(json.dumps({"architecture": args.architecture, "resolved": len(data.get("actions", {}).get("LINK", [])), "installed": False}))


if __name__ == "__main__":
    main()
