#!/usr/bin/env python3
"""Relocate only selected build outputs and their explicit clean dependency graph."""

import argparse
import json
from pathlib import Path

from ksi_local.native_staging import stage_native_graph


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("libraries", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--ffmpeg-build", required=True, type=Path)
    parser.add_argument("--imagemagick-build", required=True, type=Path)
    args = parser.parse_args()
    selected = {"ffmpeg": args.ffmpeg_build / "build/ffmpeg", "ffprobe": args.ffmpeg_build / "build/ffprobe", "magick": args.imagemagick_build / "build/utilities/magick"}
    result = stage_native_graph(selected, args.libraries.absolute(), args.destination.absolute())
    print(json.dumps({"files": len(result["files"]), "executables": result["executables"], "acceptance_tested": False, "license_review_complete": False}))


if __name__ == "__main__":
    main()
