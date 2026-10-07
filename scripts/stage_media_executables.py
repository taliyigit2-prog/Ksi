#!/usr/bin/env python3
"""Stage native media executables against retained original source notices."""

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path

from ksi_local.atomic_files import atomic_write_json
from ksi_local.bundle_runtime import digest_file, safe_member
from ksi_local.copy_on_write import clone_file


def stage(repository, graph, originals, destination, architecture):
    if architecture not in {"arm64", "x86_64"} or destination.exists() or destination.is_symlink() or not destination.is_absolute():
        raise ValueError("Media executable stage needs an explicit architecture and new directory.")
    if graph.is_symlink() or originals.is_symlink() or not graph.is_dir() or not originals.is_dir():
        raise ValueError("Media graph and original source stages must be ordinary explicit inputs.")
    report = json.loads(safe_member(graph, "native-staging.json").read_text())
    sources = json.loads(safe_member(originals, "component-specification.json").read_text())
    if report.get("schema_version") != 1 or sources.get("schema_version") != 1 or sources.get("architecture") != architecture:
        raise ValueError("Media graph/source specification is invalid.")
    inputs = json.loads((repository / "config/native-sources.json").read_text())["inputs"]
    pins = json.loads((repository / "config/tool-source-notices.json").read_text())["sources"]
    mapping = {"ffmpeg": ("ffmpeg", "COPYING.GPLv2"), "ffprobe": ("ffmpeg", "COPYING.GPLv2"), "magick": ("imagemagick", "LICENSE")}
    plans = []
    for name, (engine, legal_name) in mapping.items():
        records = [row for row in report["files"] if row["path"] == name]
        if len(records) != 1 or name not in report.get("executables", []):
            raise ValueError("Native media executable is missing or duplicated.")
        record = records[0]
        origin = safe_member(graph, name)
        if not origin.is_file() or not os.access(origin, os.X_OK) or origin.stat().st_size != record["size"] or digest_file(origin) != record["staged_sha256"]:
            raise ValueError("Relocated native executable differs from its recorded build input.")
        actual = subprocess.run(["/usr/bin/lipo", "-archs", str(origin)], check=True,
            capture_output=True, text=True, timeout=30).stdout.split()
        if architecture not in actual:
            raise ValueError("Native media executable lacks its claimed architecture.")
        source_id = engine + "-corresponding-source"
        notice_path = "licenses/tools/" + engine + "-corresponding/" + legal_name
        notices = [row for row in sources["files"] if row["role"] == "license" and row["path"] == notice_path]
        archives = [row for row in sources["files"] if row["role"] == "support" and row["identifier"] == source_id]
        if len(notices) != 1 or len(archives) != 1 or archives[0]["sha256"] != pins[source_id]["source_archive_sha256"]:
            raise ValueError("Native media source/notice is missing or differs from its public pin.")
        for row in (notices[0], archives[0]):
            path = safe_member(originals, row["path"])
            if not path.is_file() or path.stat().st_size != row["size"] or digest_file(path) != row["sha256"]:
                raise ValueError("Native media source/notice changed before tool staging.")
        engine_pin = inputs[engine + "-source"]
        if inputs[source_id]["commit"] != engine_pin["commit"]:
            raise ValueError("Native source archive and compiler source refer to different commits.")
        metadata = dict(license=engine_pin["license"], license_file=notices[0]["identifier"],
            source_url=engine_pin["url"], revision=engine_pin["commit"])
        if engine == "ffmpeg":
            metadata["corresponding_source"] = source_id
        plans.append((origin, record, name, metadata))
    destination.mkdir(parents=True, mode=0o700)
    rows = []
    for origin, record, name, metadata in plans:
        relative = "engines/media/" + name
        target = safe_member(destination, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not clone_file(origin, target):
            shutil.copy2(origin, target)
        target.chmod(0o755)
        if target.stat().st_size != record["size"] or digest_file(target) != record["staged_sha256"]:
            raise ValueError("Native media executable changed while copying.")
        rows.append(dict(path=relative, role="tool", identifier=name, size=record["size"],
            sha256=record["staged_sha256"], **metadata))
    atomic_write_json(destination / "component-specification.json", {"schema_version": 1,
        "architecture": architecture, "files": rows})
    return {"architecture": architecture, "tools": len(rows), "acceptance_tested": False,
        "redistribution_review_complete": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("architecture", choices=("arm64", "x86_64"))
    parser.add_argument("graph", type=Path)
    parser.add_argument("originals", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(json.dumps(stage(Path(__file__).resolve().parents[1], args.graph.absolute(), args.originals.absolute(),
        args.destination.absolute(), args.architecture)))


if __name__ == "__main__":
    main()
