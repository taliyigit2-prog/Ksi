#!/usr/bin/env python3
"""Seal explicit clean build inputs; never collects a user's runtime or HOME."""

import argparse
import json
from pathlib import Path

from ksi_local.offline_build import seal_offline_payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("resources", type=Path)
    parser.add_argument("specification", type=Path)
    args = parser.parse_args()
    if args.specification.is_symlink() or args.specification.stat().st_size > 8 * 1024**2:
        raise ValueError("Yapı tanımı boyut sınırı aşıyor veya symlink.")
    specification = json.loads(args.specification.read_text(encoding="utf-8"))
    print(json.dumps(seal_offline_payload(args.resources, specification), ensure_ascii=False))


if __name__ == "__main__":
    main()
