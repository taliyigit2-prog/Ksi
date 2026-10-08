#!/usr/bin/env python3
"""Bind explicit verified auxiliary build inputs; not release/legal acceptance."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

from ksi_local.atomic_files import atomic_write_bytes, atomic_write_json
from ksi_local.app_assembly import committed_file
from ksi_local.bundle_runtime import digest_file, safe_member
from ksi_local.copy_on_write import clone_file


def stage(repository, architecture, destination):
    if destination.exists() or destination.is_symlink() or not destination.is_absolute():
        raise ValueError("Auxiliary components require a new explicit directory")
    build = repository / "build"
    inputs = json.loads((repository / "config/native-sources.json").read_text())["inputs"]
    originals = build / ("tool-original-sources-" + architecture + "-v4")
    spec = json.loads((originals / "component-specification.json").read_text())
    source_rows = {row["path"]: row for row in spec["files"]}
    plan = []
    extra = []

    def notice(path):
        row = source_rows[path]
        file = safe_member(originals, path)
        if file.stat().st_size != row["size"] or digest_file(file) != row["sha256"]:
            raise ValueError("Original auxiliary notice changed")
        return row["identifier"]

    ollama_root = build / "native-staged" / ("ollama-" + architecture)
    record = json.loads((ollama_root / "tool-staging.json").read_text())
    if record["architecture"] != architecture or record["archive_sha256"] != inputs["ollama-macos"]["sha256"]:
        raise ValueError("Ollama stage differs from its pinned release")
    ollama_license = notice("licenses/tools/ollama/LICENSE")
    for row in record["files"]:
        origin = safe_member(ollama_root, row["path"])
        if origin.stat().st_size != row["size"] or digest_file(origin) != row["staged_sha256"]:
            raise ValueError("Ollama runtime member changed")
        identifier = "ollama" if row["path"] == "ollama" else "ollama-runtime-" + hashlib.sha256(row["path"].encode()).hexdigest()[:16]
        plan.append((origin, "engines/ollama/" + row["path"], "tool" if identifier == "ollama" else "support", identifier,
                     dict(license="MIT", license_file=ollama_license, source_url=inputs["ollama-source"]["url"], revision=inputs["ollama-source"]["commit"])))

    deno = build / "native-staged" / ("deno-" + architecture + "-adhoc")
    record = json.loads((deno / "tool-staging.json").read_text())
    pin = inputs["deno-" + architecture]
    if record["sha256"] != pin["sha256"] or record["extracted_sha256"] != digest_file(deno / "deno"):
        raise ValueError("Ad-hoc Deno input changed")
    plan.append((deno / "deno", "engines/download/deno", "tool", "deno", dict(license="MIT",
                 license_file=notice("licenses/tools/deno/LICENSE.md"), source_url=pin["url"], revision=pin["revision"])))

    yt = build / "native-cache/yt-dlp_macos"
    pin = inputs["yt-dlp-macos"]
    if yt.stat().st_size != pin["size"] or digest_file(yt) != pin["sha256"]:
        raise ValueError("Frozen yt-dlp input changed")
    # Upstream README explicitly licenses the combined PyInstaller executable
    # GPLv3+. The original aggregate and source are retained, but transitive
    # corresponding-source review is STILL a separate release prerequisite.
    plan.append((yt, "engines/download/yt-dlp", "tool", "yt-dlp", dict(license="GPL-3.0-or-later",
                 license_file=notice("licenses/tools/yt-dlp/THIRD_PARTY_LICENSES.txt"),
                 corresponding_source="yt-dlp-source", source_url=pin["url"], revision=pin["revision"])))

    ox = build / "native-staged" / ("oxipng-" + architecture)
    pin = inputs["oxipng-" + architecture]
    archive = build / "native-cache" / Path(pin["url"]).name
    if archive.stat().st_size != pin["size"] or digest_file(archive) != pin["sha256"]:
        raise ValueError("Oxipng original archive is missing or changed")
    with tarfile.open(archive, "r:gz") as original:
        for name in ("oxipng", "LICENSE"):
            members = [item for item in original if item.isfile() and Path(item.name).name == name]
            if len(members) != 1:
                raise ValueError("Oxipng original member is ambiguous")
            content = original.extractfile(members[0]).read()
            if hashlib.sha256(content).hexdigest() != digest_file(ox / name):
                raise ValueError("Oxipng staged member differs from its original archive")
    plan.append((ox / "LICENSE", "licenses/tools/oxipng/LICENSE", "license", "oxipng-original-license", {}))
    plan.append((ox / "oxipng", "engines/images/oxipng", "tool", "oxipng", dict(license="MIT",
                 license_file="oxipng-original-license", source_url=pin["url"], revision=pin["revision"])))

    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip()
    own_license, _ = committed_file(repository, commit, "LICENSE")
    extra.append((own_license, "licenses/ksi/LICENSE", "license", "ksi-project-license"))
    ocr = build / "ci-ocr-inputs" / ("native-ocr-" + architecture) / "KSIOCR"
    # Native helper source is part of the same public project. Keep it inside
    # the component inventory along with the actual CI-produced executable.
    helper_source, _ = committed_file(repository, commit, "native/KSIOCR.swift")
    extra.append((helper_source, "sources/ksi/KSIOCR.swift", "support", "ocr-original-source"))
    plan.append((ocr, "engines/ocr/KSIOCR", "tool", "ocr-helper", dict(license="Apache-2.0", license_file="ksi-project-license",
                 source_url="https://github.com/taliyigit2-prog/Ksi", revision=commit)))
    font = build / "portable-font-arm64-v2"
    font_record = json.loads((font / "font-staging.json").read_text())
    for row in font_record["files"]:
        origin = font / row["path"]
        if origin.stat().st_size != row["size"] or digest_file(origin) != row["sha256"]:
            raise ValueError("Portable font or original license changed")
        role, identifier = {"DejaVuSans.ttf": ("support", "subtitle-font"), "fonts.conf": ("support", "fontconfig-config"),
                            "LICENSE.DejaVu": ("license", "subtitle-font-original-license")}[row["path"]]
        plan.append((origin, "engines/fonts/" + row["path"], role, identifier, {}))
    if architecture == "x86_64":
        graph = build / "ci-native-verified-x86_64/native-artifact"
        plan.append((graph / "LICENSE.whisper", "licenses/tools/whisper-cpp/LICENSE", "license", "whisper-cpp-original-license", {}))
        plan.append((graph / "whisper-cli", "engines/speech/whisper-cli", "tool", "whisper-cli", dict(license="MIT",
                     license_file="whisper-cpp-original-license", source_url=inputs["whisper-source"]["url"], revision=inputs["whisper-source"]["commit"])))
    destination.mkdir(parents=True, mode=0o700)
    rows = []
    for origin, relative, role, identifier, metadata in plan:
        if origin.is_symlink() or not origin.is_file():
            raise ValueError("Auxiliary source must be an ordinary file")
        if role == "tool":
            actual = subprocess.check_output(["/usr/bin/lipo", "-archs", str(origin)], text=True).split()
            if architecture not in actual:
                raise ValueError("Auxiliary executable lacks its target architecture")
        target = safe_member(destination, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not clone_file(origin, target):
            shutil.copy2(origin, target)
        target.chmod(0o755 if role == "tool" or os.access(origin, os.X_OK) else 0o644)
        if digest_file(origin) != digest_file(target):
            raise ValueError("Auxiliary member changed while copying")
        rows.append(dict(path=relative, role=role, identifier=identifier, size=target.stat().st_size, sha256=digest_file(target), **metadata))
    for content, relative, role, identifier in extra:
        target = safe_member(destination, relative)
        atomic_write_bytes(target, content, mode=0o644)
        rows.append(dict(path=relative, role=role, identifier=identifier, size=len(content), sha256=digest_file(target)))
    atomic_write_json(destination / "component-specification.json", dict(schema_version=1, architecture=architecture, files=rows), mode=0o644)
    return dict(architecture=architecture, files=len(rows), acceptance_tested=False, redistribution_review_complete=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("architecture", choices=("arm64", "x86_64"))
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(json.dumps(stage(Path(__file__).resolve().parents[1], args.architecture, args.destination.absolute())))
