#!/usr/bin/env python3
"""Preserve actual native library notices, exact recipes and source archives."""

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

from ksi_local.atomic_files import atomic_write_json
from ksi_local.bundle_runtime import digest_file, safe_member
from ksi_local.copy_on_write import clone_file
from ksi_local.native_attribution import inventory_native_attribution


def stage(lock, sources, notices, graph, source_cache, destination):
    if destination.exists() or destination.is_symlink() or not destination.is_absolute():
        raise FileExistsError("Native library components need a new explicit destination.")
    if sources.get("schema_version") != 1 or source_cache.is_symlink() or not source_cache.is_dir():
        raise ValueError("Native corresponding source inputs are invalid.")
    inventory = inventory_native_attribution(lock, notices, graph,
        notice_fallbacks={"libfreetype": "freetype", "libfreetype6": "freetype"})
    if inventory["missing_license_texts"]:
        raise ValueError("Native libraries lack original notices.")
    rows, plan, seen = [], [], {}

    def add(origin, relative, pin, role, identifier, **metadata):
        safe_member(destination, relative)
        if origin.is_symlink() or not origin.is_file() or origin.stat().st_size != pin["size"] or digest_file(origin) != pin["sha256"]:
            raise ValueError("Native library/source/notice changed before component staging.")
        row = dict(path=relative, role=role, identifier=identifier, size=pin["size"], sha256=pin["sha256"], **metadata)
        previous = seen.get(relative.casefold())
        if previous is not None:
            if previous != row:
                raise ValueError("Native attribution component paths collide.")
            return identifier
        seen[relative.casefold()] = row
        rows.append(row)
        plan.append((origin, relative))
        return identifier

    for library in inventory["libraries"]:
        notices_ids = []
        for file in library["license_files"]:
            identifier = "native-notice-" + hashlib.sha256(file["path"].encode()).hexdigest()[:24]
            notices_ids.append(add(safe_member(notices, file["path"]), "licenses/native/" + file["path"], file, "license", identifier))
        for file in library["recipe_files"]:
            add(safe_member(notices, file["path"]), "sources/native-recipes/" + file["path"], file, "support",
                "native-recipe-" + hashlib.sha256(file["path"].encode()).hexdigest()[:24])
        metadata = {"license": library["declared_license"], "license_file": notices_ids[0],
            "source_url": library["archive_url"], "revision": library["archive_sha256"]}
        if re.search(r"\b(?:A?GPL|LGPL)(?:-|\b)", library["declared_license"]):
            source = sources["sources"].get(library["package"])
            if source is None or source["version"] != library["version"]:
                raise ValueError("Copyleft native library lacks exact corresponding-source pin.")
            if not any(source["sha256"].encode() in safe_member(notices, file["path"]).read_bytes() for file in library["recipe_files"]):
                raise ValueError("Corresponding-source digest is not bound to the actual locked recipe.")
            source_id = "native-source-" + library["package"]
            add(safe_member(source_cache, source["filename"]), "sources/native/" + source["filename"], source, "support", source_id)
            metadata["corresponding_source"] = source_id
        add(safe_member(graph, library["path"]), "engines/media/" + library["path"],
            {"size": library["size"], "sha256": library["staged_sha256"]}, "support",
            "native-library-" + hashlib.sha256(library["path"].encode()).hexdigest()[:24], **metadata)
    destination.mkdir(parents=True, mode=0o700)
    for origin, relative in plan:
        target = safe_member(destination, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not clone_file(origin, target):
            shutil.copy2(origin, target)
        target.chmod(0o644)
    spec = {"schema_version": 1, "architecture": lock["architecture"], "files": rows}
    atomic_write_json(destination / "component-specification.json", spec, mode=0o644)
    return {"architecture": lock["architecture"], "libraries": len(inventory["libraries"]),
        "files": len(rows), "required_source_archives_present": True, "redistribution_review_complete": False,
        "acceptance_tested": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("architecture", choices=("arm64", "x86_64"))
    parser.add_argument("build", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[1]
    build = args.build.absolute()
    if build.is_symlink() or not build.is_dir():
        raise ValueError("Native build root is not a normal explicit directory.")
    inputs = build / ("ci-native-verified-" + args.architecture)
    print(json.dumps(stage(json.loads((repository / f"config/native-libraries-{args.architecture}.json").read_text()),
        json.loads((repository / "config/native-corresponding-sources.json").read_text()),
        inputs / "native-notices", inputs / "native-relocated", build / "corresponding-source", args.destination.absolute())))


if __name__ == "__main__":
    main()
