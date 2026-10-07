#!/usr/bin/env python3
"""Fetch exact Go module sources named by a pinned executable's build info.

This never runs the executable or compiles module code. Downloads require an
explicit flag and use a dedicated public module cache, not the user's GOPATH.
Go verifies module h1 sums; we additionally bind each sum to upstream go.sum.
The result is source/notice inventory, not blanket redistribution approval.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ksi_local.atomic_files import atomic_write_bytes, atomic_write_json
from ksi_local.bundle_runtime import digest_file, safe_member
from ksi_local.copy_on_write import clone_file


def binary_modules(binary: Path, source: Path, architecture: str, revision: str):
    output = subprocess.run(["go", "version", "-m", str(binary)], check=True,
        capture_output=True, text=True, timeout=30).stdout
    expected_arch = "arm64" if architecture == "arm64" else "amd64"
    build = {}
    for line in output.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[0] == "build" and "=" in fields[1]:
            key, value = fields[1].split("=", 1)
            if key in build:
                raise ValueError("Executable build metadata is duplicated.")
            build[key] = value
    if build.get("GOARCH") != expected_arch or build.get("vcs.revision") != revision:
        raise ValueError("Executable build info is not the pinned native architecture/revision.")
    sums = set((source / "go.sum").read_text().splitlines())
    modules = []
    for line in output.splitlines():
        fields = line.split()
        if fields and fields[0] == "=>":
            raise ValueError("Unreviewed Go module replacements are not permitted.")
        if not fields or fields[0] != "dep":
            continue
        if len(fields) != 4 or not re.fullmatch(r"[A-Za-z0-9./_+-]+", fields[1]) or not fields[2].startswith("v"):
            raise ValueError("Executable Go dependency record is invalid.")
        name, version, checksum = fields[1:]
        if f"{name} {version} {checksum}" not in sums:
            raise ValueError("Executable module is not bound to official upstream go.sum.")
        modules.append((name, version, checksum))
    if not 1 <= len(modules) <= 300 or len(set(modules)) != len(modules):
        raise ValueError("Executable dependency inventory is empty or duplicated.")
    return modules


def fetch(binary: Path, source: Path, destination: Path, architecture: str):
    repository = Path(__file__).resolve().parents[1]
    pin = json.loads((repository / "config/native-sources.json").read_text())["inputs"]["ollama-source"]
    provenance = json.loads((source / "ksi-source-provenance.json").read_text())
    if provenance.get("commit") != pin["commit"] or provenance.get("url") != pin["url"]:
        raise ValueError("Go source provenance differs from the official tool pin.")
    reviewed = {row["path"]: row["sha256"] for row in provenance["files"]}
    if digest_file(source / "go.sum") != reviewed.get("go.sum"):
        raise ValueError("Official Go module checksums changed.")
    notice_pin = json.loads((repository / "config/tool-source-notices.json").read_text())["sources"]["ollama-source"]
    archive = safe_member(source, "corresponding-source.tar")
    if digest_file(archive) != notice_pin["source_archive_sha256"]:
        raise ValueError("Go checksums lack the reviewed corresponding-source archive.")
    with tarfile.open(archive, "r:*") as original:
        members = [row for row in original.getmembers() if row.name.removeprefix("./") == "go.sum"]
        if len(members) != 1 or not members[0].isfile() or members[0].size > 4 * 1024**2:
            raise ValueError("Original Go checksum member is missing or unsafe.")
        stream = original.extractfile(members[0])
        with stream:
            if hashlib.sha256(stream.read()).hexdigest() != reviewed["go.sum"]:
                raise ValueError("Go checksums differ from the reviewed original archive.")
    modules = binary_modules(binary, source, architecture, pin["commit"])
    if destination.exists() or destination.is_symlink() or not destination.is_absolute():
        raise FileExistsError("Go notices need a new explicit destination.")
    destination.mkdir(parents=True, mode=0o700)
    cache = destination / "public-module-cache"
    environment = os.environ.copy()
    environment.update(GOPATH=str(cache), GOMODCACHE=str(cache / "pkg/mod"),
        GOPROXY="https://proxy.golang.org", GOSUMDB="sum.golang.org", GOPRIVATE="",
        GONOPROXY="", GONOSUMDB="", GOTOOLCHAIN="local", GOENV="off", GOFLAGS="", GOWORK="off")

    def download(module):
        name, version, checksum = module
        process = subprocess.run(["go", "mod", "download", "-json", name + "@" + version],
            cwd=destination, env=environment, check=True, capture_output=True, text=True, timeout=240)
        record = json.loads(process.stdout)
        if record.get("Error") or record.get("Path") != name or record.get("Version") != version or record.get("Sum") != checksum:
            raise ValueError("Downloaded Go module differs from the executable checksum.")
        archive = Path(record["Zip"])
        if archive.is_symlink() or not archive.is_file() or not archive.resolve().is_relative_to(cache):
            raise ValueError("Go module archive escaped its dedicated cache.")
        identifier = hashlib.sha256((name + "@" + version).encode()).hexdigest()[:24]
        stored = destination / "sources" / (identifier + ".zip")
        stored.parent.mkdir(exist_ok=True)
        if not clone_file(archive, stored):
            shutil.copy2(archive, stored)
        notices, seen = [], set()
        with zipfile.ZipFile(stored) as stream:
            if len(stream.infolist()) > 100000 or sum(row.file_size for row in stream.infolist()) > 512 * 1024**2:
                raise ValueError("Go module expansion exceeds the notice inventory bound.")
            for member in stream.infolist():
                basename = Path(member.filename).name.casefold()
                if member.is_dir() or not basename.startswith(("license", "licence", "copying", "copyright", "notice")):
                    continue
                if Path(member.filename).suffix.casefold() not in {"", ".txt", ".md", ".rst"}:
                    continue
                if member.file_size > 4 * 1024**2:
                    raise ValueError("Go notice exceeds its bound.")
                if (member.external_attr >> 16) & 0o170000 == 0o120000 or member.filename.casefold() in seen:
                    raise ValueError("Go notice is linked or duplicated.")
                seen.add(member.filename.casefold())
                data = stream.read(member)
                data.decode("utf-8")
                path = "licenses/" + identifier + "/" + member.filename
                target = safe_member(destination, path)
                target.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_bytes(target, data)
                notices.append({"path": path, "size": len(data), "sha256": digest_file(target)})
        if not notices:
            raise ValueError("Go dependency lacks original legal notices: " + name)
        return {"module": name, "version": version, "h1": checksum,
            "source_archive": stored.relative_to(destination).as_posix(), "source_archive_sha256": digest_file(stored),
            "source_archive_size": stored.stat().st_size, "notices": notices}

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(download, modules))
    result = {"schema_version": 1, "architecture": architecture, "source_commit": pin["commit"],
        "binary_sha256": digest_file(binary), "modules": results,
        "redistribution_review_complete": False, "acceptance_tested": False}
    atomic_write_json(destination / "go-module-notices.json", result)
    return {"architecture": architecture, "modules": len(results),
        "notices": sum(len(row["notices"]) for row in results), "redistribution_review_complete": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("architecture", choices=("arm64", "x86_64"))
    parser.add_argument("binary", type=Path)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--download-public-sources", action="store_true", required=True)
    args = parser.parse_args()
    print(json.dumps(fetch(args.binary.absolute(), args.source.absolute(), args.destination.absolute(), args.architecture)))


if __name__ == "__main__":
    main()
