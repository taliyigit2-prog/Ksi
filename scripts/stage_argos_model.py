#!/usr/bin/env python3
"""Stage only hash-pinned Argos inference files and upstream README citations."""

import argparse
import json
from pathlib import Path

from ksi_local.model_staging import stage_argos


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("identifier", choices=["argos-en-tr", "argos-tr-en"])
    parser.add_argument("archive", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    entry = json.loads((root / "config/model-sources.json").read_text())["inputs"][args.identifier]
    pair = tuple(args.identifier.removeprefix("argos-").split("-"))
    result = stage_argos(args.archive, args.destination.absolute(), entry, pair=pair)
    print(json.dumps({"identifier": args.identifier, "verified_files": len(result["files"]),
        "acceptance_tested": False, "redistribution_review_complete": False}))


if __name__ == "__main__":
    main()
