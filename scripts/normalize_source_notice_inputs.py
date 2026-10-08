#!/usr/bin/env python3
"""Correct verified legacy build-input placeholder roles before app assembly."""
import argparse
import json
from pathlib import Path
from ksi_local.source_notice_normalization import normalize_empty_source_notices

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("components", type=Path)
    args = parser.parse_args()
    print(json.dumps(normalize_empty_source_notices(args.components.absolute())))
