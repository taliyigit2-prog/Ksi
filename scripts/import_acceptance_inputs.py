#!/usr/bin/env python3
"""Import independently checksum-reviewed native acceptance inputs, not an app."""
import argparse
import json
from pathlib import Path
from ksi_local.acceptance_inputs import import_inputs

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("architecture", choices=("arm64", "x86_64"))
    parser.add_argument("transport", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("sha256")
    args = parser.parse_args()
    print(json.dumps(import_inputs(args.transport.absolute(), args.destination.absolute(),
        args.sha256, architecture=args.architecture)))
