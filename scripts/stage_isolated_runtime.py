#!/usr/bin/env python3
"""Stage one explicit clean speech runtime, never a personal environment."""

import argparse
import hashlib
import json
from pathlib import Path

from ksi_local.app_assembly import copy_clean_tree
from ksi_local.atomic_files import atomic_write_json
from ksi_local.bundle_runtime import digest_file, safe_member
from ksi_local.wheel_lock import validate_wheel_lock


def validate_runtime(runtime, lock, python_input, source_inputs, scope):
    validate_wheel_lock(lock)
    if scope not in {"piper", "chatterbox"} or runtime.is_symlink() or not runtime.is_dir():
        raise ValueError("Isolated speech prefix/scope is invalid.")
    provenance = json.loads(safe_member(runtime, "runtime-provenance.json").read_text())
    lock_sha = hashlib.sha256(json.dumps(lock, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if provenance.get("schema_version") != 1 or provenance.get("architecture") != lock["architecture"] or provenance.get("wheel_lock_sha256") != lock_sha or provenance.get("python_source_sha256") != python_input["sha256"] or provenance.get("python_version") != python_input["version"]:
        raise ValueError("Isolated speech prefix differs from its exact public runtime inputs.")
    overrides = provenance.get("source_overrides", [])
    if scope == "chatterbox":
        if len(overrides) != 2 or {row.get("name") for row in overrides} != {"chatterbox-tts", "antlr4-python3-runtime"}:
            raise ValueError("Multilingual speech needs exact Git API and ANTLR source overrides.")
        override = next(row for row in overrides if row["name"] == "chatterbox-tts")
        expected = source_inputs["chatterbox-source"]
        if override.get("name") != "chatterbox-tts" or override.get("commit") != expected["commit"] or override.get("source_url") != expected["url"] or override.get("version") != expected["version"]:
            raise ValueError("Chatterbox source override differs from its public source pin.")
        files = override.get("files", [])
        if not files or len(files) > 5000:
            raise ValueError("Chatterbox source override inventory is incomplete.")
        seen = set()
        for row in files:
            if row["path"].casefold() in seen or not row["path"].startswith("chatterbox/"):
                raise ValueError("Chatterbox source override members collide or escape their package.")
            seen.add(row["path"].casefold())
            member = safe_member(runtime, "python/lib/python3.12/site-packages/" + row["path"])
            if not member.is_file() or digest_file(member) != row["sha256"]:
                raise ValueError("Chatterbox source override changed before staging.")
        antlr = next(row for row in overrides if row["name"] == "antlr4-python3-runtime")
        source, notice = source_inputs["antlr-python-source"], source_inputs["antlr-python-license"]
        if (antlr.get("source_url") != source["url"] or antlr.get("version") != source["version"]
                or antlr.get("source_archive_sha256") != source["sha256"] or antlr.get("license_sha256") != notice["sha256"]):
            raise ValueError("ANTLR source differs from its original public inputs")
        files = antlr.get("files", [])
        if not files or len(files) > 1000:
            raise ValueError("ANTLR source override inventory is incomplete")
        seen = set()
        for row in files:
            if row["path"].casefold() in seen or not row["path"].startswith(("antlr4/", "antlr4_python3_runtime-4.9.3.dist-info/")):
                raise ValueError("ANTLR source members collide or escape their package")
            seen.add(row["path"].casefold())
            member = safe_member(runtime, "python/lib/python3.12/site-packages/" + row["path"])
            if not member.is_file() or digest_file(member) != row["sha256"]:
                raise ValueError("ANTLR source override changed before staging")
        required = {"antlr4/__init__.py", "antlr4_python3_runtime-4.9.3.dist-info/METADATA",
                    "antlr4_python3_runtime-4.9.3.dist-info/LICENSE.txt"}
        if not {name.casefold() for name in required}.issubset(seen):
            raise ValueError("ANTLR original package metadata/notice is missing")
    elif overrides:
        raise ValueError("Piper prefix cannot carry unreviewed source overrides.")
    # Piper installs its own GPL COPYING at the prefix root. It is not
    # CPython's license; use the interpreter's original stdlib legal text.
    for member in ("python/bin/python3.12", "python/lib/python3.12/LICENSE.txt"):
        path = safe_member(runtime, member)
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError("Isolated Python interpreter or original license text is missing.")
    return provenance


def stage(runtime, lock, python_input, source_inputs, scope, destination):
    if not destination.is_absolute() or destination.exists() or destination.is_symlink():
        raise FileExistsError("Isolated runtime needs a new explicit component stage.")
    validate_runtime(runtime, lock, python_input, source_inputs, scope)
    destination.mkdir(parents=True, mode=0o700)
    prefix = destination / "engines" / scope
    prefix.parent.mkdir()
    copy_clean_tree(runtime, prefix)
    validate_runtime(prefix, lock, python_input, source_inputs, scope)
    rows = []
    license_id = scope + "-python-original-notice"
    for path in sorted(prefix.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(destination).as_posix()
        suffix = path.relative_to(prefix).as_posix()
        row = dict(path=relative, role="support", identifier="isolated-" + scope + "-" + hashlib.sha256(suffix.encode()).hexdigest()[:24],
            sha256=digest_file(path), size=path.stat().st_size)
        if suffix == "python/lib/python3.12/LICENSE.txt":
            row.update(role="license", identifier=license_id)
        elif scope == "chatterbox" and suffix == "python/lib/python3.12/site-packages/antlr4_python3_runtime-4.9.3.dist-info/LICENSE.txt":
            row.update(role="license", identifier="chatterbox-antlr-original-notice")
        elif suffix == "python/COPYING" and scope == "piper":
            row.update(role="license", identifier="piper-engine-original-notice")
        elif suffix == "python/bin/python3.12":
            row.update(role="tool", identifier=scope + "-python", license="PSF-2.0",
                license_file=license_id, source_url=python_input["url"], revision=python_input["sha256"])
        rows.append(row)
    if len(rows) > 40000:
        raise ValueError("Isolated runtime exceeds the explicit component bound.")
    atomic_write_json(destination / "component-specification.json", {"schema_version": 1,
        "architecture": lock["architecture"], "files": rows})
    return {"architecture": lock["architecture"], "scope": scope, "files": len(rows),
        "acceptance_tested": False, "redistribution_review_complete": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("architecture", choices=("arm64", "x86_64"))
    parser.add_argument("scope", choices=("chatterbox", "piper"))
    parser.add_argument("runtime", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[1]
    lock = json.loads((repository / f"config/python-{args.scope}-wheels-{args.architecture}.json").read_text())
    python_input = json.loads((repository / "config/runtime-sources.json").read_text())["inputs"]["python-" + args.architecture]
    sources = json.loads((repository / "config/native-sources.json").read_text())["inputs"]
    print(json.dumps(stage(args.runtime.absolute(), lock, python_input, sources, args.scope, args.destination.absolute())))


if __name__ == "__main__":
    main()
