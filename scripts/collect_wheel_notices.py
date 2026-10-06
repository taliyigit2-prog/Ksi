#!/usr/bin/env python3
"""Preserve wheel licenses with exact public artifact provenance."""

import argparse
import json
from pathlib import Path

from ksi_local.wheel_notices import collect_wheel_notices


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("lock", type=Path)
    parser.add_argument("wheelhouse", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    result = collect_wheel_notices(json.loads(args.lock.read_text()), args.wheelhouse.absolute(), args.destination.absolute())
    print(json.dumps({"architecture": result["architecture"], "packages": len(result["packages"]),
        "missing_license_texts": result["missing_license_texts"], "redistribution_review_complete": False}))


if __name__ == "__main__":
    main()
