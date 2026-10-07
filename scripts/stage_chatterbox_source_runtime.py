#!/usr/bin/env python3
"""Bind the isolated speech prefix to exact upstream V3 source, not wheel API.

PyPI 0.1.7 and the selected Git commit share a version but differ in API. Keep
the original clean prefix intact, record the source override explicitly and
regenerate the distribution RECORD for the new generated prefix.
"""

import argparse
import base64
import csv
import hashlib
import io
import json
import shutil
from pathlib import Path

from ksi_local.app_assembly import copy_clean_tree
from ksi_local.antlr_source import install_source
from ksi_local.atomic_files import atomic_write_json, atomic_write_text
from ksi_local.bundle_runtime import digest_file
from ksi_local.native_build import _stage_native_source


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("runtime", type=Path)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--antlr-source", type=Path, required=True)
    parser.add_argument("--antlr-license", type=Path, required=True)
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[1]
    inputs = json.loads((repository / "config/native-sources.json").read_text())["inputs"]
    source_pin = inputs["chatterbox-source"]
    destination = args.destination.absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Source-bound speech runtime requires new staging.")
    source_stage = destination.with_name(destination.name + "-source")
    verified = _stage_native_source(args.source.absolute(), source_stage, source_pin["commit"])
    provenance = json.loads((args.runtime / "runtime-provenance.json").read_text())
    lock = json.loads((repository / "config/python-chatterbox-wheels-arm64.json").read_text())
    expected_lock = hashlib.sha256(json.dumps(lock, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if provenance.get("architecture") != "arm64" or provenance.get("wheel_lock_sha256") != expected_lock:
        raise ValueError("Source-bound speech runtime has different public dependencies.")
    copy_clean_tree(args.runtime.absolute(), destination)
    packages = destination / "python/lib/python3.12/site-packages"
    package = packages / "chatterbox"
    # Delete only the original wheel package in this newly created copy. The
    # input prefix and source are preserved and can reproduce this step.
    if not package.is_dir() or package.is_symlink():
        raise ValueError("Fresh speech prefix has no normal Chatterbox package.")
    shutil.rmtree(package)
    copy_clean_tree(verified / "src/chatterbox", package)
    metadata = packages / "chatterbox_tts-0.1.7.dist-info"
    if not metadata.is_dir() or metadata.is_symlink():
        raise ValueError("Speech distribution metadata is missing.")
    shutil.copy2(verified / "LICENSE", metadata / "KSI-UPSTREAM-LICENSE")
    override = {"name": "chatterbox-tts", "version": "0.1.7",
        "source_url": source_pin["url"], "commit": source_pin["commit"],
        "integration": "Exact upstream source replaces same-version PyPI package API",
        "files": [{"path": path.relative_to(packages).as_posix(), "sha256": digest_file(path)}
                  for path in sorted(package.rglob("*")) if path.is_file()],
        "acceptance_tested": False}
    atomic_write_json(metadata / "KSI-UPSTREAM-SOURCE.json", override, mode=0o644)
    atomic_write_json(metadata / "direct_url.json", {"url": source_pin["url"],
        "vcs_info": {"vcs": "git", "commit_id": source_pin["commit"], "requested_revision": source_pin["commit"]}}, mode=0o644)
    rows = []
    record = metadata / "RECORD"
    for folder in (package, metadata):
        for path in sorted(folder.rglob("*")):
            if path.is_file() and path != record:
                digest = base64.urlsafe_b64encode(bytes.fromhex(digest_file(path))).rstrip(b"=").decode("ascii")
                rows.append([path.relative_to(packages).as_posix(), "sha256=" + digest, str(path.stat().st_size)])
    rows.append([record.relative_to(packages).as_posix(), "", ""])
    buffer = io.StringIO(newline="")
    csv.writer(buffer).writerows(rows)
    atomic_write_text(record, buffer.getvalue(), mode=0o644)
    antlr = install_source(args.antlr_source.absolute(), args.antlr_license.absolute(), packages,
                           inputs["antlr-python-source"], inputs["antlr-python-license"])
    provenance["source_overrides"] = [override, antlr]
    atomic_write_json(destination / "runtime-provenance.json", provenance, mode=0o644)
    print(json.dumps({"architecture": "arm64", "source_commit": source_pin["commit"],
        "upstream_files": len(override["files"]), "acceptance_tested": False}))


if __name__ == "__main__":
    main()
