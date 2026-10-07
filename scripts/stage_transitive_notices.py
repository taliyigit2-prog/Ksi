#!/usr/bin/env python3
"""Preserve exact collected Go sources and supplementary Cargo root notices."""

import argparse
import hashlib
import json
import runpy
import shutil
from pathlib import Path

from ksi_local.atomic_files import atomic_write_json
from ksi_local.bundle_runtime import digest_file, safe_member
from ksi_local.copy_on_write import clone_file


def stage(repository, architecture, destination):
    if destination.exists() or destination.is_symlink() or not destination.is_absolute():
        raise ValueError("Transitive notices require a new explicit directory")
    build = repository / "build"
    go_root = build / ("ollama-go-notices-" + architecture)
    report = json.loads((go_root / "go-module-notices.json").read_text())
    binary = build / ("ollama-" + architecture + "-buildinfo")
    source = build / "native-source/ollama-notices-pristine"
    pin = json.loads((repository / "config/native-sources.json").read_text())["inputs"]["ollama-source"]
    if report["architecture"] != architecture or report["source_commit"] != pin["commit"] or report["binary_sha256"] != digest_file(binary):
        raise ValueError("Go notice inventory is not bound to the pinned native binary")
    inspect = runpy.run_path(str(repository / "scripts/fetch_go_binary_notices.py"))["binary_modules"]
    modules = inspect(binary, source, architecture, pin["commit"])
    if set(modules) != {(row["module"], row["version"], row["h1"]) for row in report["modules"]}:
        raise ValueError("Go notice inventory differs from the actual executable dependencies")
    cargo_root = build / "deno-cargo-root-notices"
    cargo = json.loads((cargo_root / "cargo-root-source-notices.json").read_text())
    deno = build / "native-source/deno-notices-pristine"
    if cargo["cargo_lock_sha256"] != digest_file(deno / "Cargo.lock"):
        raise ValueError("Supplementary Cargo notices use another exact source lock")
    plans = []
    for module in report["modules"]:
        if not module["notices"]:
            raise ValueError("A compiled Go dependency has no original notice")
        for row in module["notices"]:
            plans.append((go_root, row["path"], "licenses/go/" + row["path"], "license", row["sha256"], row["size"]))
        plans.append((go_root, module["source_archive"], "sources/go/" + module["source_archive"], "support",
                      module["source_archive_sha256"], module["source_archive_size"]))
    plans.append((go_root, "go-module-notices.json", "licenses/go/dependency-inventory.json", "support",
                  digest_file(go_root / "go-module-notices.json"), (go_root / "go-module-notices.json").stat().st_size))
    for group in cargo["groups"]:
        for row in group["notices"]:
            plans.append((cargo_root, row["path"], "licenses/cargo-root/" + row["path"], "license", row["sha256"], row["size"]))
    plans.append((cargo_root, "cargo-root-source-notices.json", "licenses/cargo-root/dependency-inventory.json", "support",
                  digest_file(cargo_root / "cargo-root-source-notices.json"), (cargo_root / "cargo-root-source-notices.json").stat().st_size))
    # Absent root notices are recorded honestly in the original report. These
    # supplemental files do not claim that build/test crates ship in the binary.
    rows = []
    destination.mkdir(parents=True, mode=0o700)
    for root, name, relative, role, expected, size in plans:
        origin = safe_member(root, name)
        if not origin.is_file() or origin.stat().st_size != size or digest_file(origin) != expected:
            raise ValueError("Original transitive notice/source changed")
        target = safe_member(destination, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not clone_file(origin, target):
            shutil.copy2(origin, target)
        target.chmod(0o644)
        rows.append(dict(path=relative, role=role, identifier="transitive-" + hashlib.sha256(relative.encode()).hexdigest()[:24],
                         sha256=expected, size=size))
    atomic_write_json(destination / "component-specification.json", dict(schema_version=1, architecture=architecture, files=rows), mode=0o644)
    return dict(architecture=architecture, files=len(rows), compiled_go_modules=len(modules), redistribution_review_complete=False, acceptance_tested=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("architecture", choices=("arm64", "x86_64"))
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(json.dumps(stage(Path(__file__).resolve().parents[1], args.architecture, args.destination.absolute())))
