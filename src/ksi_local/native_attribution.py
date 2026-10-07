"""Bind the shipped native dependency graph to exact package notices/recipes.

Package ownership comes from locked archive paths metadata, not guessed library
names. Attribution completeness is not corresponding-source or legal approval.
"""

import json
from pathlib import Path

from ksi_local.bundle_runtime import digest_file, safe_member
from ksi_local.native_packages import validate_native_lock


def _read(path: Path) -> dict:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 16 * 1024**2:
        raise ValueError("Native attribution metadata is missing, linked or oversized.")
    return json.loads(path.read_text(encoding="utf-8"))


def inventory_native_attribution(lock: dict, notices: Path, graph: Path, *, notice_fallbacks: dict | None = None) -> dict:
    if any(path.is_symlink() or not path.is_dir() for path in (notices, graph)):
        raise ValueError("Native attribution needs explicit ordinary staging roots.")
    locked = {row["name"]: row for row in validate_native_lock(lock)}
    receipt = _read(safe_member(notices, "native-notices.json"))
    if receipt.get("schema_version") != 1 or receipt.get("architecture") != lock["architecture"]:
        raise ValueError("Native notice receipt differs from the target architecture.")
    packages, owners = {}, {}
    for package in receipt["packages"]:
        expected = locked.get(package["name"])
        if expected is None or package["name"] in packages or any(package.get(key) != expected[field] for key, field in
                (("version", "version"), ("archive_sha256", "sha256"), ("archive_url", "url"), ("license_declared", "license"))):
            raise ValueError("Native notice receipt differs from the exact public package lock.")
        packages[package["name"]] = package
        for file in package["files"]:
            path = safe_member(notices, file["path"])
            if not path.is_file() or path.stat().st_size != file["size"] or digest_file(path) != file["sha256"]:
                raise ValueError("Native original notice/recipe metadata was changed.")
            if file["path"].endswith("/info/paths.json"):
                data = _read(path)
                if data.get("paths_version") != 1 or not isinstance(data.get("paths"), list) or len(data["paths"]) > 100000:
                    raise ValueError("Native package paths inventory is invalid.")
                for member in data["paths"]:
                    relative = member["_path"]
                    safe_member(graph, relative)
                    owners.setdefault(relative, set()).add(package["name"])
    if set(packages) != set(locked):
        raise ValueError("Native attribution receipt does not cover the complete lock.")
    fallback = notice_fallbacks or {}
    graph_receipt = _read(safe_member(graph, "native-staging.json"))
    if graph_receipt.get("schema_version") != 1 or not isinstance(graph_receipt.get("files"), list):
        raise ValueError("Native graph provenance version is invalid.")
    result, missing, seen = [], set(), set()
    for file in graph_receipt["files"]:
        relative = file["path"]
        if relative in seen:
            raise ValueError("Native graph contains duplicate paths.")
        seen.add(relative)
        path = safe_member(graph, relative)
        if not path.is_file() or path.stat().st_size != file["size"] or digest_file(path) != file["staged_sha256"]:
            raise ValueError("Native graph changed after its signed staging receipt.")
        if not relative.startswith("lib/"):
            continue  # Independently compiled engines have their own source pins.
        candidates = owners.get(relative, set())
        if len(candidates) != 1:
            raise ValueError("Native library has missing or ambiguous package ownership: " + relative)
        name = next(iter(candidates))
        package = packages[name]
        selected = package
        originals = [row for row in selected["files"] if "/info/licenses/" in row["path"] and row["size"] > 0]
        if not originals and name in fallback:
            selected = packages.get(fallback[name])
            if selected is None or selected["version"] != package["version"]:
                raise ValueError("Split-package license fallback has another source/version.")
            originals = [row for row in selected["files"] if "/info/licenses/" in row["path"] and row["size"] > 0]
        if not originals:
            missing.add(name)
        result.append({"path": relative, "staged_sha256": file["staged_sha256"], "size": file["size"],
            "package": name, "version": package["version"], "declared_license": package["license_declared"],
            "archive_sha256": package["archive_sha256"], "archive_url": package["archive_url"],
            "notice_package": selected["name"], "license_files": originals,
            "recipe_files": [row for row in package["files"] if "/info/recipe/" in row["path"]]})
    return {"schema_version": 1, "architecture": lock["architecture"], "libraries": result,
        "missing_license_texts": sorted(missing), "corresponding_sources_complete": False,
        "redistribution_review_complete": False}
