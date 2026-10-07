#!/usr/bin/env python3
"""Merge only explicit reviewed component inventories into a new stage."""

import argparse
import json
from pathlib import Path

from ksi_local.component_staging import merge_components


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("architecture", choices=("arm64", "x86_64"))
    parser.add_argument("destination", type=Path)
    parser.add_argument("pieces", nargs="+", type=Path)
    args = parser.parse_args()
    print(json.dumps(merge_components(tuple(path.absolute() for path in args.pieces),
        args.destination.absolute(), architecture=args.architecture)))


if __name__ == "__main__":
    main()
