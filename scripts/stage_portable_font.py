#!/usr/bin/env python3
"""Stage one pinned subtitle font with its exact collected upstream notice."""

import argparse
import json
import shutil
from pathlib import Path

from ksi_local.atomic_files import atomic_write_json
from ksi_local.bundle_runtime import digest_file, safe_member


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("prefix", type=Path)
    parser.add_argument("notices", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    if args.destination.exists() or args.destination.is_symlink() or args.prefix.is_symlink() or args.notices.is_symlink():
        raise ValueError("Font staging requires explicit normal inputs and a new destination.")
    font = safe_member(args.prefix, "fonts/DejaVuSans.ttf")
    if not font.is_file() or font.stat().st_size != 757076 or digest_file(font) != "7da195a74c55bef988d0d48f9508bd5d849425c1770dba5d7bfc6ce9ed848954":
        raise ValueError("Portable font differs from its pinned public package member.")
    package = next(row for row in json.loads((args.notices / "native-notices.json").read_text())["packages"] if row["name"] == "font-ttf-dejavu-sans-mono")
    record = next(row for row in package["files"] if row["path"].endswith("/info/licenses/LICENSE"))
    license_file = safe_member(args.notices, record["path"])
    if license_file.stat().st_size != record["size"] or digest_file(license_file) != record["sha256"]:
        raise ValueError("Portable font license differs from collected archive evidence.")
    config = Path(__file__).resolve().parents[1] / "packaging/fontconfig/fonts.xml"
    args.destination.mkdir(parents=True)
    for origin, name in ((font, "DejaVuSans.ttf"), (license_file, "LICENSE.DejaVu"), (config, "fonts.conf")):
        shutil.copyfile(origin, args.destination / name)
        (args.destination / name).chmod(0o644)
    result = {"schema_version": 1, "archive_url": package["archive_url"], "archive_sha256": package["archive_sha256"], "files": [{"path": name, "size": (args.destination / name).stat().st_size, "sha256": digest_file(args.destination / name)} for name in ("DejaVuSans.ttf", "LICENSE.DejaVu", "fonts.conf")], "acceptance_tested": False, "license_review_complete": False}
    atomic_write_json(args.destination / "font-staging.json", result)
    print(json.dumps({"files": len(result["files"]), "acceptance_tested": False}))


if __name__ == "__main__":
    main()
