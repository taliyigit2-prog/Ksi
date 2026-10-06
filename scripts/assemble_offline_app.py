#!/usr/bin/env python3
"""Build an ad-hoc signed app from explicit clean, locked staging inputs."""

import argparse
import json
from pathlib import Path

from ksi_local.app_assembly import assemble_app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("runtime", type=Path)
    parser.add_argument("components", type=Path)
    parser.add_argument("specification", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    spec = json.loads(args.specification.read_text(encoding="utf-8"))
    lock = json.loads((root / f'config/python-wheels-{spec["architecture"]}.json').read_text(encoding="utf-8"))
    print(json.dumps(assemble_app(root, args.runtime.absolute(), args.components.absolute(), spec, args.destination.absolute(), wheel_lock=lock)))


if __name__ == "__main__":
    main()
