#!/usr/bin/env python3
"""Package an explicitly supplied clean, sealed app. Not a personal-app copier."""

import argparse
import json
from pathlib import Path

from ksi_local.dmg_transport import build_dmg_transport


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("application", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(json.dumps(build_dmg_transport(args.application, args.destination), ensure_ascii=False))


if __name__ == "__main__":
    main()
