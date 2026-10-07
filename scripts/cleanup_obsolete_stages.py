#!/usr/bin/env python3
"""Verify explicit obsolete build stages; --apply requires user approval."""

import argparse
import json
from pathlib import Path

from ksi_local.build_cleanup import cleanup_obsolete_stages


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = cleanup_obsolete_stages(Path(__file__).resolve().parents[1], apply=args.apply)
    print(json.dumps({"applied": result["applied"], "removed": result.get("removed", []),
                      "verified_payload_bytes": sum(item["verified_payload_bytes"] for item in result["candidates"])}))


if __name__ == "__main__":
    main()
