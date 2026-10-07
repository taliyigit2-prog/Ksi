#!/usr/bin/env python3
"""Combine exact wheel notices with version-bound public supplemental sources."""

import argparse
import json
from pathlib import Path

from ksi_local.bundle_runtime import digest_file, safe_member
from ksi_local.atomic_files import atomic_write_json
from ksi_local.distribution_notices import stage_distribution_notices


def read(path):
    if path.is_symlink() or path.stat().st_size > 16 * 1024**2:
        raise ValueError("Notice receipt is linked or oversized.")
    return json.loads(path.read_text(encoding="utf-8"))


def source_rows(build, name, archive_root, archive_name, digest, url):
    archive = safe_member(archive_root, archive_name)
    if not archive.is_file() or digest_file(archive) != digest:
        raise ValueError("Supplement archive differs from its official source pin.")
    collection = build / "source-license-texts" / (name + "-complete" if name in {"qtbase", "pyside-setup"} else name)
    receipt = read(collection / "source-licenses.json")
    if receipt.get("schema_version") != 1 or receipt.get("source_archive_sha256") != digest or receipt.get("source_archive_name") != archive_name:
        raise ValueError("Collected notice text has a different corresponding source.")
    rows = [dict(row, root=collection, kind="license", source_url=url,
        target="licenses/source/" + name + "/" + row["path"]) for row in receipt["files"]]
    rows.append({"root": archive_root, "path": archive_name, "sha256": digest,
        "size": archive.stat().st_size, "kind": "source", "source_url": url,
        "target": "sources/python/" + archive_name})
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("architecture", choices=("arm64", "x86_64"))
    parser.add_argument("scope", choices=("main", "piper", "chatterbox"))
    parser.add_argument("build", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[1]
    build = args.build.absolute()
    if build.is_symlink() or not build.is_dir():
        raise ValueError("Build inputs must have an explicit ordinary root.")
    suffix = "" if args.scope == "main" else "-" + args.scope
    lock = read(repository / f"config/python{suffix}-wheels-{args.architecture}.json")
    directory = build / f"wheel-notices-{args.scope}-{args.architecture}"
    report = read(directory / "wheel-notices.json")
    supplements = {}
    needed = {row["name"]: row["version"] for row in report["packages"] if not row["license_text_present"]}
    inputs = read(repository / "config/python-notice-sources.json")["inputs"]
    for name, version in needed.items():
        if name in {"PySide6_Essentials", "shiboken6"}:
            qt = read(repository / "config/qt-corresponding-sources.json")
            if version != qt["version"]:
                raise ValueError("Qt sources do not match this wheel version.")
            rows = []
            for source, digest in qt["sources"].items():
                filename = source + "-everywhere-src-" + version + ".tar.xz"
                url = qt["pyside_base_url" if source == "pyside-setup" else "qt_base_url"] + filename
                rows.extend(source_rows(build, source, build / "corresponding-source" / ("qt-" + version), filename, digest, url))
        else:
            candidates = [(key, row) for key, row in inputs.items() if row["package"] == name and row["package_version"] == version]
            if len(candidates) != 1:
                raise ValueError("No exact supplemental public notice source: " + name)
            key, source = candidates[0]
            if source["type"] == "sdist":
                rows = source_rows(build, key, build / "python-notice-sources", key + ".tar.gz", source["sha256"], source["url"])
            else:
                rows = [{"root": build / "python-notice-sources", "path": key + ".txt",
                    "target": "licenses/source/" + key + ".txt", "kind": "license",
                    "sha256": source["sha256"], "size": source["size"], "source_url": source["url"]}]
        normalized = name.lower().replace("_", "-")
        supplements[normalized] = {"version": version, "files": rows}
    result = stage_distribution_notices(lock, report, directory, supplements, args.destination.absolute(), namespace=args.scope)
    inventory = args.destination.absolute() / "python-notice-inventory.json"
    rows = list(result["files"])
    # Keep each isolated prefix's inventory distinct while allowing identical
    # original notices/sources to be deduplicated when component lists merge.
    inventory_name = f"licenses/python-inventory-{args.scope}.json"
    inventory.rename(safe_member(args.destination.absolute(), inventory_name))
    inventory = safe_member(args.destination.absolute(), inventory_name)
    rows.append({"path": inventory_name, "role": "support",
        "identifier": "python-notice-inventory-" + args.scope,
        "sha256": digest_file(inventory), "size": inventory.stat().st_size})
    atomic_write_json(args.destination.absolute() / "component-specification.json",
        {"schema_version": 1, "architecture": args.architecture, "files": rows}, mode=0o644)
    print(json.dumps({"architecture": args.architecture, "scope": args.scope,
        "packages": len(result["packages"]), "files": len(result["files"]),
        "missing_license_texts": result["missing_license_texts"], "redistribution_review_complete": False}))


if __name__ == "__main__":
    main()
