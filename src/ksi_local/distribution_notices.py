"""Bind every locked wheel to verified, redistributable original notice text.

This closes missing-text inventory gaps, not the separate binary/source/license
review. Inputs are explicit clean build directories; no installed environment
or user configuration is read. Copied source archives remain intact.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from urllib.parse import urlsplit

from ksi_local.atomic_files import atomic_write_json
from ksi_local.bundle_runtime import digest_file, safe_member
from ksi_local.copy_on_write import clone_file
from ksi_local.wheel_lock import validate_wheel_lock


def _name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def validate_notice_inventory(resources: Path, specification: dict, lock: dict, *, scope: str) -> None:
    """Require complete notice coverage inside the actual staged application."""
    identifier = "python-notice-inventory-" + scope
    matches = [row for row in specification["files"] if row["role"] == "support" and row["identifier"] == identifier]
    if len(matches) != 1:
        raise ValueError("App is missing its exact Python notice inventory: " + scope)
    entry = matches[0]
    path = safe_member(resources, entry["path"])
    if not path.is_file() or path.stat().st_size != entry["size"] or entry["size"] > 16 * 1024**2 or digest_file(path) != entry["sha256"]:
        raise ValueError("Python notice inventory integrity failed.")
    report = json.loads(path.read_text(encoding="utf-8"))
    locked = {_name(row["name"]): row for row in validate_wheel_lock(lock)}
    if report.get("schema_version") != 1 or report.get("architecture") != lock["architecture"] or report.get("missing_license_texts") != []:
        raise ValueError("Python notice inventory has unresolved texts or wrong architecture.")
    indexed = {row["path"]: row for row in specification["files"]}
    seen = set()
    packages = report.get("packages")
    if not isinstance(packages, list) or len(packages) != len(locked):
        raise ValueError("Python notice inventory does not cover the exact wheel set.")
    for package in packages:
        name = _name(package["name"])
        expected = locked.get(name)
        if expected is None or name in seen or any(package.get(key) != expected[field] for key, field in
                (("version", "version"), ("wheel_sha256", "sha256"), ("source_url", "url"))):
            raise ValueError("App Python notices differ from the locked package.")
        seen.add(name)
        notices = package.get("notices")
        if not isinstance(notices, list) or not any(row.get("kind") == "license" for row in notices):
            raise ValueError("App Python package is missing original license text.")
        for notice in notices:
            bound = indexed.get(notice["target"])
            role = "license" if notice.get("kind") == "license" else "support"
            if bound is None or bound["role"] != role or any(bound[key] != notice[key] for key in ("sha256", "size")):
                raise ValueError("App Python notice/source is not bound to its sealed inventory.")
            original = safe_member(resources, notice["target"])
            if not original.is_file() or original.stat().st_size != bound["size"] or digest_file(original) != bound["sha256"]:
                raise ValueError("App Python notice/source integrity failed.")


def stage_distribution_notices(lock: dict, wheel_report: dict, wheel_directory: Path,
                               supplements: dict, destination: Path, *, namespace: str = "main") -> dict:
    """Supplements map exact package versions to original pinned text/source rows.

    Each row supplies a root directory, relative path, expected SHA-256/size,
    public provenance URL, and kind ('license' or 'source'). Local roots are
    deliberately omitted from generated provenance.
    """
    if namespace not in {"main", "piper", "chatterbox"}:
        raise ValueError("Python notice namespace is invalid.")
    locked = {_name(row["name"]): row for row in validate_wheel_lock(lock)}
    if wheel_report.get("schema_version") != 1 or wheel_report.get("architecture") != lock["architecture"]:
        raise ValueError("Notice inventory architecture/version differs from the wheel lock.")
    packages = wheel_report.get("packages")
    if not isinstance(packages, list) or len(packages) != len(locked):
        raise ValueError("Notice inventory does not cover the exact locked package set.")
    if destination.exists() or destination.is_symlink() or not destination.is_absolute():
        raise FileExistsError("Distribution notices require a new explicit staging directory.")
    if wheel_directory.is_symlink() or not wheel_directory.is_dir():
        raise ValueError("Wheel notices must come from a normal explicit directory.")
    plan, inventory, seen, targets = [], [], set(), {}
    for package in packages:
        name = _name(package["name"])
        expected = locked.get(name)
        if name in seen or expected is None or any(package.get(key) != expected[field] for key, field in
                (("version", "version"), ("wheel_sha256", "sha256"), ("source_url", "url"))):
            raise ValueError("Notice inventory package differs from its exact public wheel.")
        seen.add(name)
        rows = []
        for file in package.get("files", []):
            if file.get("kind") == "license":
                rows.append(dict(file, root=wheel_directory, source_url=expected["url"],
                    target="licenses/python/" + namespace + "/" + file["path"]))
        extra = supplements.get(name)
        if extra is not None:
            if extra.get("version") != expected["version"]:
                raise ValueError("Supplemental notice version differs from its wheel.")
            rows.extend(extra["files"])
        if not any(row.get("kind") == "license" and row.get("size", 0) > 0 for row in rows):
            raise ValueError("Locked package has no original license text: " + name)
        members = []
        for row in rows:
            source_url = row.get("source_url")
            parsed = urlsplit(source_url) if isinstance(source_url, str) else None
            if parsed is None or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("Notice provenance must be a clean public HTTPS source.")
            root = row["root"]
            if root.is_symlink() or not root.is_dir() or row.get("kind") not in {"license", "source"}:
                raise ValueError("Supplement source directory/kind is invalid.")
            origin = safe_member(root, row["path"])
            target = row["target"]
            safe_member(destination, target)
            if not target.startswith(("licenses/", "sources/")):
                raise ValueError("Notice staging cannot replace runtime/model members.")
            if not origin.is_file() or origin.stat().st_size != row["size"] or digest_file(origin) != row["sha256"]:
                raise ValueError("Original notice/source differs from its pinned digest.")
            if row["kind"] == "license":
                if not 0 < row["size"] <= 4 * 1024**2 or not origin.read_text(encoding="utf-8").strip():
                    raise ValueError("License text is empty, opaque or oversized.")
            identity = (row["sha256"], row["size"], row["kind"])
            previous = targets.get(target.casefold())
            if previous is not None and previous != identity:
                raise ValueError("Two notice inputs collide at their destination.")
            if previous is None:
                targets[target.casefold()] = identity
                plan.append((origin, target, row))
            members.append({key: row[key] for key in ("target", "sha256", "size", "kind", "source_url")})
        inventory.append({"name": expected["name"], "version": expected["version"],
            "wheel_sha256": expected["sha256"], "source_url": expected["url"],
            "declared_license": expected["license"], "notices": members})
    if seen != set(locked):
        raise ValueError("Notice inventory omits locked packages.")
    destination.mkdir(parents=True, mode=0o700)
    files = []
    for origin, relative, row in plan:
        target = safe_member(destination, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not clone_file(origin, target):
            shutil.copy2(origin, target)
        target.chmod(0o644)
        files.append({"path": relative, "sha256": row["sha256"], "size": row["size"],
            "role": "license" if row["kind"] == "license" else "support",
            "identifier": "python-notice-" + hashlib.sha256(relative.encode()).hexdigest()[:24]})
    result = {"schema_version": 1, "architecture": lock["architecture"], "packages": inventory,
        "files": files, "missing_license_texts": [], "redistribution_review_complete": False}
    atomic_write_json(destination / "python-notice-inventory.json", result, mode=0o644)
    return result
