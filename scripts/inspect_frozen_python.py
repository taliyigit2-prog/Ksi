#!/usr/bin/env python3
"""Read the pinned macOS download executable's actual embedded inventory."""

import argparse
import json
from pathlib import Path

from ksi_local.atomic_files import atomic_write_json
from ksi_local.frozen_inventory import inspect_frozen_archive


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    pin = json.loads((root / "config/native-sources.json").read_text())["inputs"]["yt-dlp-macos"]
    result = inspect_frozen_archive(args.executable, pin["sha256"])
    atomic_write_json(args.report, result, mode=0o600)
    print(json.dumps(dict(members=len(result["members"]), modules=len(result["modules"]),
                         metadata_packages=len(result["distribution_metadata"]),
                         dependency_versions_complete=False, redistribution_review_complete=False)))
