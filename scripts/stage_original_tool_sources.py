#!/usr/bin/env python3
"""Stage explicitly reviewed original tool notices, not a license approval.

Full commit-bound source archives are retained without executing source code.
Transitive binary dependency review is separately recorded as incomplete.
"""

import argparse
import hashlib
import json
import runpy
import shutil
from pathlib import Path

from ksi_local.atomic_files import atomic_write_json
from ksi_local.bundle_runtime import digest_file, safe_member
from ksi_local.copy_on_write import clone_file


def stage(repository: Path, source_root: Path, destination: Path, architecture: str) -> dict:
    if architecture not in {"arm64", "x86_64"}:
        raise ValueError("Tool notice architecture is invalid.")
    if not destination.is_absolute() or destination.exists() or destination.is_symlink():
        raise FileExistsError("Tool notices require a new explicit stage.")
    if source_root.is_symlink() or not source_root.is_dir():
        raise ValueError("Tool source root must be an ordinary directory.")
    pins = json.loads((repository / "config/tool-source-notices.json").read_text())
    inputs = json.loads((repository / "config/native-sources.json").read_text())["inputs"]
    if pins.get("schema_version") != 1 or pins.get("redistribution_review_complete") is not False:
        raise ValueError("Original source collection cannot grant release approval.")
    collect = runpy.run_path(str(repository / "scripts/collect_source_license_texts.py"))["collect"]
    plan = []
    for identifier, pin in pins["sources"].items():
        tool = identifier.removesuffix("-source")
        origin = safe_member(source_root, pin.get("source_directory", tool + "-notices-pristine"))
        provenance_file = safe_member(origin, "ksi-source-provenance.json")
        if not provenance_file.is_file() or provenance_file.stat().st_size > 16 * 1024**2:
            raise ValueError("Original tool source provenance is missing or oversized.")
        provenance = json.loads(provenance_file.read_text())
        expected = inputs[identifier]
        if (provenance.get("schema_version") != 1 or
                provenance.get("commit") != expected["commit"] or
                provenance.get("url") != expected["url"] or
                provenance.get("source_archive_sha256") != pin["source_archive_sha256"]):
            raise ValueError("Original tool source differs from the pinned official commit.")
        archive = safe_member(origin, "corresponding-source.tar")
        if not archive.is_file() or digest_file(archive) != pin["source_archive_sha256"]:
            raise ValueError("Original source archive changed before notice staging.")
        plan.append((identifier, tool, origin, archive, pin, expected))
    destination.mkdir(mode=0o700, parents=True)
    rows, reviews = [], []
    for identifier, tool, origin, archive, pin, expected in plan:
        notice_root = destination / "licenses/tools" / tool
        result = collect(archive, pin["source_archive_sha256"], notice_root, members=tuple(pin["members"]))
        for notice in result["files"]:
            rows.append(dict(path="licenses/tools/" + tool + "/" + notice["path"],
                role="license", identifier="tool-notice-" + tool + "-" + hashlib.sha256(notice["path"].encode()).hexdigest()[:20],
                size=notice["size"], sha256=notice["sha256"]))
        inventory = notice_root / "source-licenses.json"
        rows.append(dict(path=inventory.relative_to(destination).as_posix(), role="support",
            identifier="tool-source-notice-inventory-" + tool, size=inventory.stat().st_size, sha256=digest_file(inventory)))
        relative = "sources/tools/" + tool + "/corresponding-source.tar"
        target = safe_member(destination, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not clone_file(archive, target):
            shutil.copy2(archive, target)
        target.chmod(0o644)
        rows.append(dict(path=relative, role="support", identifier=identifier,
            size=target.stat().st_size, sha256=pin["source_archive_sha256"]))
        reviews.append(dict(identifier=tool, version=expected.get("version", expected["commit"]), revision=expected["commit"],
            source_url=expected["url"], source_archive_sha256=pin["source_archive_sha256"],
            original_notices=pin["members"], pending_closure=pin["pending_closure"],
            redistribution_review_complete=False))
    review = destination / "licenses/tools/source-review.json"
    atomic_write_json(review, {"schema_version": 1, "tools": reviews, "redistribution_review_complete": False})
    rows.append(dict(path=review.relative_to(destination).as_posix(), role="support",
        identifier="tool-original-source-review", size=review.stat().st_size, sha256=digest_file(review)))
    atomic_write_json(destination / "component-specification.json", {
        "schema_version": 1, "architecture": architecture, "files": rows})
    return {"architecture": architecture, "files": len(rows), "tools": len(reviews),
        "redistribution_review_complete": False, "acceptance_tested": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("architecture", choices=("arm64", "x86_64"))
    parser.add_argument("source_root", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(json.dumps(stage(Path(__file__).resolve().parents[1], args.source_root.absolute(),
        args.destination.absolute(), args.architecture)))


if __name__ == "__main__":
    main()
