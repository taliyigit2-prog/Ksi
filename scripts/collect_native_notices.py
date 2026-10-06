#!/usr/bin/env python3
"""Collect exact locked archive notices, never a personal package cache."""

import argparse
import json
from pathlib import Path

from ksi_local.native_notices import collect_native_notices


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("lock", type=Path)
    parser.add_argument("cache", type=Path)
    parser.add_argument("zstd", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    result = collect_native_notices(json.loads(args.lock.read_text()), args.cache.absolute(), args.zstd.absolute(), args.destination.absolute())
    print(json.dumps({"packages": len(result["packages"]), "missing_license_text": [row["name"] for row in result["packages"] if not row["license_text_present"]], "redistribution_review_complete": False}))


if __name__ == "__main__":
    main()
