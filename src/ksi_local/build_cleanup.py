"""Targeted obsolete-stage cleanup, never a whole-build or user-data purge."""

import json
import shutil
from pathlib import Path

from ksi_local.atomic_files import atomic_write_json
from ksi_local.bundle_runtime import digest_file, safe_member


REPLACEMENTS = {
    "offline-model-components-arm64": "offline-model-components-arm64-v2",
    "offline-model-components-x86_64": "offline-model-components-x86_64-v2",
    "offline-base-components-arm64": "offline-base-with-libraries-arm64",
    "offline-base-components-x86_64": "offline-base-with-libraries-x86_64",
}


def _inventory(stage):
    specification = safe_member(stage, "component-specification.json")
    if not specification.is_file() or specification.stat().st_size > 32 * 1024**2:
        raise ValueError("Component specification is missing or oversized")
    payload = json.loads(specification.read_text(encoding="utf-8"))
    rows = payload.get("files")
    if payload.get("schema_version") != 1 or not isinstance(rows, list) or not rows:
        raise ValueError("Component inventory is invalid")
    by_path = {row["path"]: row for row in rows}
    if len(by_path) != len(rows):
        raise ValueError("Duplicate component inventory paths")
    actual = set()
    for path in stage.rglob("*"):
        if path.is_symlink():
            raise ValueError("Symlinks prevent build-stage cleanup")
        if path.is_file():
            actual.add(path.relative_to(stage).as_posix())
    if actual != set(by_path) | {"component-specification.json"}:
        raise ValueError("Unlisted files prevent build-stage cleanup")
    return payload, by_path


def cleanup_obsolete_stages(project_root: Path, *, apply=False):
    root = project_root.absolute()
    if root.is_symlink() or not (root / "src/ksi_local").is_dir() or not (root / ".git").is_dir():
        raise ValueError("Expected the public KSI source repository")
    build = root / "build"
    if build.is_symlink() or not build.is_dir():
        raise ValueError("Build root must be an ordinary directory")
    report = {"schema_version": 1, "applied": False, "candidates": []}
    identities = {}
    for old, new in REPLACEMENTS.items():
        source, replacement = build / old, build / new
        if not source.exists():
            continue
        if source.is_symlink() or replacement.is_symlink() or not replacement.is_dir():
            raise ValueError("Replacement stage must exist without symlinks")
        before, old_rows = _inventory(source)
        after, new_rows = _inventory(replacement)
        if before["architecture"] != after["architecture"]:
            raise ValueError("Replacement architecture differs")
        total = 0
        for name, row in old_rows.items():
            if new_rows.get(name) != row:
                raise ValueError("Replacement is not an exact inventory superset")
            for stage in (source, replacement):
                item = safe_member(stage, name)
                if item.stat().st_size != row["size"] or digest_file(item) != row["sha256"]:
                    raise ValueError("Source or retained replacement hash does not match")
            total += row["size"]
        stat = source.stat(follow_symlinks=False)
        identities[old] = (stat.st_dev, stat.st_ino)
        report["candidates"].append({"path": "build/" + old, "replacement": "build/" + new,
                                     "verified_payload_bytes": total, "files": len(old_rows),
                                     "original_specification": before})
    evidence = build / "obsolete-stage-cleanup.local.json"
    if not report["candidates"] and evidence.exists():
        if evidence.is_symlink() or evidence.stat().st_size > 16 * 1024**2:
            raise ValueError("Cleanup evidence is unsafe or oversized")
        previous = json.loads(evidence.read_text(encoding="utf-8"))
        if previous.get("schema_version") == 1 and previous.get("applied") is True:
            return previous  # Preserve proof and recovery inventories on rerun.
    atomic_write_json(evidence, report, mode=0o600)
    if not apply:
        return report
    # Callers must explicitly opt in after approval of the previewed four
    # generated stages. Never accept arbitrary roots, globs or environment files.
    if not shutil.rmtree.avoids_symlink_attacks:
        raise RuntimeError("This runtime cannot safely remove generated stage trees")
    report["removed"] = []
    for item in report["candidates"]:
        old = Path(item["path"]).name
        source = build / old
        stat = source.stat(follow_symlinks=False)
        if source.is_symlink() or (stat.st_dev, stat.st_ino) != identities[old]:
            raise RuntimeError("Cleanup target changed after verification")
        shutil.rmtree(source)
        report["removed"].append(item["path"])
        atomic_write_json(evidence, report, mode=0o600)
    report["applied"] = True
    atomic_write_json(evidence, report, mode=0o600)
    return report
