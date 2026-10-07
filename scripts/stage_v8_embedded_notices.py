#!/usr/bin/env python3
"""Preserve original Chromium notices pinned by the incorporated V8 DEPS."""

import argparse
import base64
import hashlib
import json
import re
import tarfile
import urllib.request
from pathlib import Path

from ksi_local.atomic_files import atomic_write_bytes, atomic_write_json
from ksi_local.build_inputs import SecureRedirect
from ksi_local.bundle_runtime import digest_file, safe_member


def stage(repository, architecture, destination):
    if architecture not in {"arm64", "x86_64"} or destination.exists() or destination.is_symlink():
        raise ValueError("Embedded notice staging requires a new explicit architecture directory")
    config = json.loads((repository / "config/v8-embedded-notices.json").read_text())
    pins = json.loads((repository / "config/tool-source-notices.json").read_text())["sources"]
    source = repository / "build/native-source/v8-embedded-notices-pristine/corresponding-source.tar"
    if digest_file(source) != pins[config["v8_source_id"]]["source_archive_sha256"]:
        raise ValueError("Incorporated V8 source differs from the exact original input")
    with tarfile.open(source, "r:*") as archive:
        member = next(item for item in archive if item.name.removeprefix("./") == "DEPS")
        if not member.isfile() or member.size > 1024**2:
            raise ValueError("Original V8 DEPS is unsafe")
        with archive.extractfile(member) as stream:
            deps = stream.read().decode("utf-8")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), SecureRedirect())
    collected = []
    for notice in config["notices"]:
        revision = notice["revision"]
        if not re.fullmatch(r"[0-9a-f]{40}", revision) or revision not in deps:
            raise ValueError("Chromium dependency revision is not present in the incorporated V8 DEPS")
        if notice["url"] != notice["url"].split("/+/", 1)[0] + "/+/" + revision + "/LICENSE?format=TEXT":
            raise ValueError("Original Chromium notice URL is inconsistent")
        request = urllib.request.Request(notice["url"], headers={"User-Agent": "KSI-original-notices/1"})
        with opener.open(request, timeout=45) as response:
            encoded = response.read(2 * 1024**2 + 1)
        if len(encoded) > 2 * 1024**2:
            raise ValueError("Original Chromium notice exceeds its bound")
        data = base64.b64decode(encoded, validate=True)
        if not data or hashlib.sha256(data).hexdigest() != notice["sha256"]:
            raise ValueError("Original Chromium notice differs from its pinned digest")
        data.decode("utf-8")
        collected.append((notice, data))
    destination.mkdir(parents=True, mode=0o700)
    rows = []
    for notice, data in collected:
        relative = "licenses/v8-embedded/" + notice["id"] + "/LICENSE"
        atomic_write_bytes(safe_member(destination, relative), data, mode=0o644)
        rows.append(dict(path=relative, role="license", identifier=notice["id"] + "-original-notice",
                         sha256=notice["sha256"], size=len(data)))
    report = dict(schema_version=1, v8_source_sha256=digest_file(source),
                  deps_sha256=hashlib.sha256(deps.encode()).hexdigest(), notices=config["notices"],
                  binary_dependency_review_complete=False)
    relative = "licenses/v8-embedded/provenance.json"
    atomic_write_json(safe_member(destination, relative), report, mode=0o644)
    target = destination / relative
    rows.append(dict(path=relative, role="support", identifier="v8-embedded-notice-provenance",
                     sha256=digest_file(target), size=target.stat().st_size))
    atomic_write_json(destination / "component-specification.json",
                      dict(schema_version=1, architecture=architecture, files=rows), mode=0o644)
    return dict(architecture=architecture, original_notices=len(collected), binary_dependency_review_complete=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("architecture", choices=("arm64", "x86_64"))
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(json.dumps(stage(Path(__file__).resolve().parents[1], args.architecture, args.destination.absolute())))
