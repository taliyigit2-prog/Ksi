#!/usr/bin/env python3
"""Collect missing original root notices from crate-bound GitHub commits.

Only repositories and commits recorded inside verified public crate archives
are used. Results are explicit review inputs, not legal compatibility approval.
No source code is run. Network access requires an explicit CLI flag.
"""

import argparse
import hashlib
import json
import re
import runpy
import tarfile
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from ksi_local.atomic_files import atomic_write_bytes, atomic_write_json
from ksi_local.bundle_runtime import digest_file, safe_member


def source_identity(repository, source, components):
    locked = runpy.run_path(str(repository / "scripts/stage_cargo_source_notices.py"))["locked_packages"]
    packages, commit, lock_sha = locked(repository, source)
    pins = {(row["name"], row["version"]): row["checksum"] for row in packages}
    inventory = json.loads((components / "licenses/cargo/workspace-source-inventory.json").read_text())
    spec = json.loads((components / "component-specification.json").read_text())
    if inventory.get("source_commit") != commit or inventory.get("cargo_lock_sha256") != lock_sha:
        raise ValueError("Cargo notice inventory differs from the pinned original source.")
    files = {row["identifier"]: row for row in spec["files"]}
    groups, unavailable = {}, []
    missing = set(inventory["missing_package_root_notice_texts"])
    for package in inventory["packages"]:
        name = package["name"] + "@" + package["version"]
        if name not in missing:
            continue
        row = files[package["source_archive"]]
        archive = safe_member(components, row["path"])
        pin = pins[(package["name"], package["version"])]
        if digest_file(archive) != pin or row["sha256"] != pin:
            raise ValueError("Cargo source changed before upstream notice collection.")
        prefix = package["name"] + "-" + package["version"] + "/"
        with tarfile.open(archive, "r:*") as stream:
            def read(relative):
                member = stream.getmember(prefix + relative)
                if not member.isfile() or member.size > 1024**2:
                    raise ValueError("Cargo public provenance member is linked or oversized.")
                content = stream.extractfile(member)
                with content:
                    return content.read()
            metadata = tomllib.loads(read("Cargo.toml").decode())["package"]
            try:
                vcs = json.loads(read(".cargo_vcs_info.json"))
            except KeyError:
                unavailable.append({"package": name, "reason": "No exact upstream Git commit in the original crate"})
                continue
        url = urlsplit(metadata.get("repository", ""))
        revision = vcs.get("git", {}).get("sha1", "")
        if url.hostname != "github.com" or url.scheme not in {"https", "http"} or url.username or url.password or url.query or url.fragment or not re.fullmatch(r"/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?/?", url.path) or not re.fullmatch(r"[a-f0-9]{40}", revision):
            unavailable.append({"package": name, "reason": "Repository/commit requires separate official-source review"})
            continue
        repository_path = url.path.rstrip("/").removesuffix(".git").lstrip("/")
        group = groups.setdefault((repository_path, revision), {"packages": [], "paths": set()})
        group["packages"].append({"name": name, "source_archive_sha256": pin, "declared_license": package["declared_license"]})
        subdirectory = vcs.get("path_in_vcs", "").strip("/")
        if subdirectory:
            safe_member(components, subdirectory)
        for filename in ("LICENSE", "LICENSE-MIT", "LICENSE-APACHE", "LICENSE.txt", "LICENSE.md", "COPYING", "COPYRIGHT"):
            group["paths"].add(filename)
            if subdirectory:
                group["paths"].add(subdirectory + "/" + filename)
    return groups, unavailable, commit, lock_sha


def fetch(repository, source, components, destination):
    if not destination.is_absolute() or destination.exists() or destination.is_symlink():
        raise FileExistsError("Original crate notices require a new explicit destination.")
    groups, unavailable, commit, lock_sha = source_identity(repository, source, components)
    destination.mkdir(parents=True, mode=0o700)

    def collect(item):
        (repo, revision), group = item
        key = hashlib.sha256((repo + "@" + revision).encode()).hexdigest()[:24]
        notices, errors = [], []
        for relative in sorted(group["paths"]):
            url = "https://raw.githubusercontent.com/" + repo + "/" + revision + "/" + relative
            try:
                with urlopen(Request(url, headers={"User-Agent": "KSI-public-source-notice-builder/1"}), timeout=30) as response:
                    final = urlsplit(response.url)
                    if final.scheme != "https" or final.hostname != "raw.githubusercontent.com" or response.url != url:
                        raise ValueError("Original GitHub notice redirected away from its exact public commit/path.")
                    content = response.read(4 * 1024**2 + 1)
                if not content or len(content) > 4 * 1024**2 or b"\x00" in content:
                    raise ValueError("Original legal text is empty, binary or oversized.")
                content.decode("utf-8")
            except HTTPError as error:
                if error.code == 404:
                    continue
                errors.append({"path": relative, "reason": "HTTP " + str(error.code)})
                continue
            except (OSError, UnicodeError) as error:
                errors.append({"path": relative, "reason": type(error).__name__})
                continue
            path = "original-notices/" + key + "/" + relative
            target = safe_member(destination, path)
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(target, content)
            notices.append({"path": path, "source_url": url, "sha256": digest_file(target), "size": len(content)})
        return {"repository": "https://github.com/" + repo, "revision": revision,
            "packages": group["packages"], "notices": notices, "fetch_errors": errors,
            "redistribution_review_complete": False}

    with ThreadPoolExecutor(max_workers=4) as executor:
        collected = list(executor.map(collect, groups.items()))
    atomic_write_json(destination / "cargo-root-source-notices.json", {"schema_version": 1,
        "source_commit": commit, "cargo_lock_sha256": lock_sha, "groups": collected,
        "unavailable_exact_sources": unavailable, "redistribution_review_complete": False})
    return {"groups": len(collected), "original_notices": sum(len(row["notices"]) for row in collected),
        "groups_without_notices": sum(not row["notices"] for row in collected),
        "unavailable_exact_sources": len(unavailable), "redistribution_review_complete": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("components", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--download-public-notices", action="store_true", required=True)
    args = parser.parse_args()
    print(json.dumps(fetch(Path(__file__).resolve().parents[1], args.source.absolute(), args.components.absolute(), args.destination.absolute())))


if __name__ == "__main__":
    main()
