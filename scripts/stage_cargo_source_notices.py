#!/usr/bin/env python3
"""Preserve checksum-bound registry sources and notices from a tool workspace.

This is a conservative source superset, not the linked executable dependency
graph. Build/test-only crates are not represented as shipped runtime packages.
No Cargo code, build scripts, or native binaries are executed here.
"""

import argparse
import hashlib
import json
import runpy
import shutil
import tarfile
import tomllib
from pathlib import Path

from ksi_local.atomic_files import atomic_write_json
from ksi_local.bundle_runtime import digest_file, safe_member
from ksi_local.copy_on_write import clone_file


def locked_packages(repository: Path, source: Path):
    inputs = json.loads((repository / "config/native-sources.json").read_text())["inputs"]["deno-source"]
    pin = json.loads((repository / "config/tool-source-notices.json").read_text())["sources"]["deno-source"]
    provenance = json.loads(safe_member(source, "ksi-source-provenance.json").read_text())
    if provenance.get("commit") != inputs["commit"] or provenance.get("url") != inputs["url"]:
        raise ValueError("Cargo source provenance differs from the pinned official tool.")
    original = safe_member(source, "corresponding-source.tar")
    if digest_file(original) != pin["source_archive_sha256"]:
        raise ValueError("Cargo source archive differs from its reviewed digest.")
    with tarfile.open(original, "r:*") as archive:
        members = [row for row in archive.getmembers() if row.name.removeprefix("./") == "Cargo.lock"]
        if len(members) != 1 or not members[0].isfile() or members[0].size > 4 * 1024**2:
            raise ValueError("Original Cargo lock is missing or unsafe.")
        stream = archive.extractfile(members[0])
        with stream:
            data = stream.read()
    lock = safe_member(source, "Cargo.lock")
    if lock.read_bytes() != data:
        raise ValueError("Cargo.lock changed during source collection.")
    packages = tomllib.loads(data.decode())["package"]
    registry = []
    for package in packages:
        origin = package.get("source")
        if origin is None:  # Local workspace source is in the original archive.
            continue
        if origin != "registry+https://github.com/rust-lang/crates.io-index":
            raise ValueError("Unreviewed Cargo source registry or Git dependency.")
        name, version = package["name"], package["version"]
        safe_member(Path("/private/tmp/ksi-cargo-member-validation"), name + "-" + version + ".crate")
        registry.append(package)
    return registry, inputs["commit"], hashlib.sha256(data).hexdigest()


def stage(repository: Path, source: Path, cache: Path, destination: Path, architecture: str):
    if architecture not in {"arm64", "x86_64"} or cache.is_symlink() or not cache.is_dir():
        raise ValueError("Cargo source cache or architecture is invalid.")
    if not destination.is_absolute() or destination.exists() or destination.is_symlink():
        raise FileExistsError("Cargo source notices require a new explicit stage.")
    packages, revision, lock_sha256 = locked_packages(repository, source)
    collect = runpy.run_path(str(repository / "scripts/collect_source_license_texts.py"))["collect"]
    archives = {}
    for path in cache.glob("registry/cache/*/*.crate"):
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(cache.resolve()):
            raise ValueError("Cargo registry archive escaped its dedicated public cache.")
        if path.name in archives:
            raise ValueError("Different registries collide at a Cargo archive name.")
        archives[path.name] = path
    plan = []
    for package in packages:
        filename = package["name"] + "-" + package["version"] + ".crate"
        archive = archives.get(filename)
        if archive is None:
            raise ValueError("Locked Cargo source is not downloaded: " + filename)
        if digest_file(archive) != package["checksum"]:
            raise ValueError("Cargo archive differs from the original lock checksum: " + filename)
        with tarfile.open(archive, "r:*") as stream:
            manifest = stream.getmember(package["name"] + "-" + package["version"] + "/Cargo.toml")
            if not manifest.isfile() or manifest.size > 1024**2:
                raise ValueError("Cargo package metadata is linked or oversized.")
            content = stream.extractfile(manifest)
            with content:
                metadata = tomllib.loads(content.read().decode())["package"]
        if metadata["name"] != package["name"] or metadata["version"] != package["version"]:
            raise ValueError("Cargo package metadata differs from the locked archive identity.")
        plan.append((package, archive, metadata))
    destination.mkdir(parents=True, mode=0o700)
    rows, inventory, missing, missing_root = [], [], [], []
    for package, archive, metadata in plan:
        identifier = hashlib.sha256((package["name"] + "@" + package["version"]).encode()).hexdigest()[:24]
        relative = "sources/cargo/" + archive.name
        target = safe_member(destination, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not clone_file(archive, target):
            shutil.copy2(archive, target)
        target.chmod(0o644)
        rows.append(dict(path=relative, role="support", identifier="cargo-source-" + identifier,
            sha256=package["checksum"], size=target.stat().st_size))
        notice_root = destination / "licenses/cargo" / identifier
        try:
            result = collect(archive, package["checksum"], notice_root)
        except ValueError as error:
            # Only absent original text is an inventory gap. Unsafe members,
            # changed archives or invalid text still abort the entire stage.
            if str(error) != "Source archive has no original license files to preserve.":
                raise
            result = {"files": []}
            missing.append(package["name"] + "@" + package["version"])
        notice_ids, root_notice_ids = [], []
        for notice in result["files"]:
            notice_id = "cargo-notice-" + hashlib.sha256((identifier + "/" + notice["path"]).encode()).hexdigest()[:24]
            notice_ids.append(notice_id)
            if Path(notice["path"]).parent.as_posix() == package["name"] + "-" + package["version"]:
                root_notice_ids.append(notice_id)
            rows.append(dict(path="licenses/cargo/" + identifier + "/" + notice["path"],
                role="license", identifier=notice_id, sha256=notice["sha256"], size=notice["size"]))
        if result["files"]:
            notice_manifest = notice_root / "source-licenses.json"
            rows.append(dict(path=notice_manifest.relative_to(destination).as_posix(), role="support",
                identifier="cargo-notice-inventory-" + identifier, sha256=digest_file(notice_manifest), size=notice_manifest.stat().st_size))
        if not root_notice_ids:
            missing_root.append(package["name"] + "@" + package["version"])
        inventory.append(dict(name=package["name"], version=package["version"], checksum=package["checksum"],
            declared_license=metadata.get("license"), declared_license_file=metadata.get("license-file"),
            source_archive="cargo-source-" + identifier, notices=notice_ids, root_notices=root_notice_ids))
    summary = destination / "licenses/cargo/workspace-source-inventory.json"
    atomic_write_json(summary, {"schema_version": 1, "source_commit": revision, "cargo_lock_sha256": lock_sha256,
        "coverage": "locked-workspace-source-superset", "packages": inventory, "missing_original_notice_texts": missing,
        "missing_package_root_notice_texts": missing_root,
        "shipped_binary_dependency_closure_complete": False, "redistribution_review_complete": False})
    rows.append(dict(path=summary.relative_to(destination).as_posix(), role="support",
        identifier="cargo-workspace-source-inventory", sha256=digest_file(summary), size=summary.stat().st_size))
    atomic_write_json(destination / "component-specification.json", {"schema_version": 1,
        "architecture": architecture, "files": rows})
    return {"architecture": architecture, "packages": len(inventory), "files": len(rows),
        "missing_original_notice_texts": missing, "shipped_binary_dependency_closure_complete": False,
        "missing_package_root_notice_count": len(missing_root),
        "redistribution_review_complete": False, "acceptance_tested": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("architecture", choices=("arm64", "x86_64"))
    parser.add_argument("source", type=Path)
    parser.add_argument("cache", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(json.dumps(stage(Path(__file__).resolve().parents[1], args.source.absolute(), args.cache.absolute(),
        args.destination.absolute(), args.architecture)))


if __name__ == "__main__":
    main()
