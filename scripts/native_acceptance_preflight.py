#!/usr/bin/env python3
"""Read-only native runner inventory; never confuse it with product acceptance."""

import argparse
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from ksi_local.atomic_files import atomic_write_json
from ksi_local.native_processor import is_rosetta_translated


def inspect(root, expected_architecture):
    if sys.platform != "darwin" or platform.machine() != expected_architecture or is_rosetta_translated():
        raise ValueError("The requested actual native Mac processor is unavailable")
    source = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    disk = shutil.disk_usage(root)
    # Full media, an independently installed app and private first-run model
    # copies must coexist. This is conservative, not a measured product pass.
    required = 60 * 1024**3
    return dict(schema_version=1, source_commit=source, architecture=expected_architecture,
                native_process=True, rosetta_translated=False, available_disk_bytes=disk.free,
                full_install_working_budget_bytes=required, disk_budget_sufficient=disk.free >= required,
                product_acceptance_performed=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("architecture", choices=("arm64", "x86_64"))
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    result = inspect(Path(__file__).resolve().parents[1], args.architecture)
    atomic_write_json(args.report, result, mode=0o600)
    print(json.dumps(result))
