#!/usr/bin/env python3
"""Create an audited source-only public release tree; never initializes Git."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from ksi_local.release_prep import build_public_tree


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("destination")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    report = build_public_tree(root, args.destination)
    print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
