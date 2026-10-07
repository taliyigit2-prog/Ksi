#!/usr/bin/env python3
"""Prepare one pinned native component; never install into the user's system."""

import argparse
import json
import subprocess
from pathlib import Path

from ksi_local.bundle_runtime import host_architecture
from ksi_local.native_build import build_raster_media_engine, build_whisper_cpu, extract_oxipng, fetch_git_source


def main():
    parser = argparse.ArgumentParser()
    actions = parser.add_subparsers(dest="action", required=True)
    source = actions.add_parser("fetch-whisper-source")
    source.add_argument("destination", type=Path)
    generic = actions.add_parser("fetch-source")
    generic.add_argument("identifier", choices=["whisper-source", "ffmpeg-source", "imagemagick-source", "piper-source", "piper-espeak-source", "chatterbox-source", "ollama-source", "deno-source", "yt-dlp-source", "rusty-v8-source", "v8-embedded-source", "ollama-mlx-c-source", "ollama-mlx-source"])
    generic.add_argument("destination", type=Path)
    raster = actions.add_parser("build-engine")
    raster.add_argument("engine", choices=["ffmpeg", "imagemagick"])
    raster.add_argument("source", type=Path)
    raster.add_argument("libraries", type=Path)
    raster.add_argument("destination", type=Path)
    whisper = actions.add_parser("build-whisper")
    whisper.add_argument("source", type=Path)
    whisper.add_argument("cmake", type=Path)
    whisper.add_argument("destination", type=Path)
    oxipng = actions.add_parser("extract-oxipng")
    oxipng.add_argument("archive", type=Path)
    oxipng.add_argument("destination", type=Path)
    oxipng.add_argument("--architecture", choices=("arm64", "x86_64"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    inputs = json.loads((root / "config/native-sources.json").read_text(encoding="utf-8"))["inputs"]
    entry = inputs[args.identifier] if args.action == "fetch-source" else inputs[args.engine + "-source"] if args.action == "build-engine" else inputs["whisper-source"]
    if args.action in {"fetch-whisper-source", "fetch-source"}:
        record = fetch_git_source(entry["url"], tag=entry["revision"], commit=entry["commit"], destination=args.destination.absolute(), notice_source_only=entry.get("notice_source_only", False))
        print(json.dumps({"commit": record["commit"], "files": len(record["files"])}))
    elif args.action == "build-engine":
        print(json.dumps(build_raster_media_engine(args.engine, args.source.absolute(), args.libraries.absolute(), args.destination.absolute(), commit=entry["commit"])))
    elif args.action == "build-whisper":
        print(build_whisper_cpu(args.source.absolute(), cmake=args.cmake.absolute(), destination=args.destination.absolute(), commit=entry["commit"]))
    else:
        architecture = args.architecture or host_architecture()
        entry = inputs["oxipng-" + architecture]
        executable = extract_oxipng(args.archive.absolute(), sha256=entry["sha256"], destination=args.destination.absolute())
        actual = subprocess.run(["/usr/bin/lipo", "-archs", str(executable)], check=True,
            capture_output=True, text=True, timeout=30).stdout.split()
        if architecture not in actual:
            raise ValueError("Oxipng native binary differs from its pinned architecture.")
        print(json.dumps({"architecture": architecture, "staged": True, "acceptance_tested": False}))


if __name__ == "__main__":
    main()
