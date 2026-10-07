#!/usr/bin/env python3
"""Bind original public model notices to exact clean build members.

These bindings supply evidence, never license-approval or personal acceptance.
The separate model builder verifies each input again before copying weights.
"""

import argparse
import json
import shutil
from pathlib import Path

from ksi_local.atomic_files import atomic_write_json
from ksi_local.bundle_runtime import digest_file, safe_member
from ksi_local.copy_on_write import clone_file
from ksi_local.wheel_lock import validate_wheel_lock


def read(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 8 * 1024**2:
        raise ValueError("Model notice evidence is linked, missing or oversized.")
    return json.loads(path.read_text(encoding="utf-8"))


def build_bindings(repository, build, destination):
    if build.is_symlink() or not build.is_dir() or destination.exists() or destination.is_symlink():
        raise ValueError("Model notice binding requires explicit clean inputs and a new file.")
    pins = read(repository / "config/model-sources.json")["inputs"]
    files = []

    def add(identifier, relative, pin):
        original = safe_member(build, relative)
        if not original.is_file() or original.stat().st_size != pin["size"] or digest_file(original) != pin["sha256"]:
            raise ValueError("Model license evidence differs from its source pin: " + identifier)
        if not 0 < pin["size"] <= 256 * 1024 or not original.read_text(encoding="utf-8").strip():
            raise ValueError("Model notice must be original bounded inert text.")
        files.append({"identifier": identifier, "input": relative, "path": "licenses/models/" + identifier + ".txt",
            "size": pin["size"], "sha256": pin["sha256"]})

    ollama = read(repository / "config/ollama-model-sources.json")["models"]
    for model in ollama:
        original = next(row for row in model["layers"] if row["role"] == "license")
        add("gemma-original-license" if model["name"] == "translategemma" else "qwen-original-license",
            "model-ollama/blobs/sha256-" + original["sha256"], original)
    snapshots = read(repository / "config/model-notice-snapshots.json")["snapshots"]
    for identifier, pin in snapshots.items():
        root = "model-legal-snapshots/" + identifier + "/"
        receipt = read(safe_member(build, root + "snapshot.json"))
        if any(receipt.get(key) != pin[key] for key in ("text_sha256", "text_size", "source_url", "retrieved_at")):
            raise ValueError("Official model terms snapshot differs from its observed public pin.")
        add(identifier, root + identifier + ".txt", {"sha256": pin["text_sha256"], "size": pin["text_size"]})
    for identifier, filename in {"gemma-notice": "Gemma-NOTICE.txt", "ksi-model-terms": "MODEL-TERMS.txt", "argos-model-notice": "Argos-MODEL-NOTICE.txt"}.items():
        original = safe_member(repository, "packaging/legal/" + filename)
        generated = safe_member(build, "model-static-notices/" + filename)
        if not original.is_file() or original.stat().st_size > 256 * 1024:
            raise ValueError("KSI model notice source is missing or oversized.")
        pin = {"size": original.stat().st_size, "sha256": digest_file(original)}
        generated.parent.mkdir(parents=True, exist_ok=True)
        if generated.exists():
            if generated.stat().st_size != pin["size"] or digest_file(generated) != pin["sha256"]:
                raise FileExistsError("Existing static notice staging is not overwritten.")
        else:
            if not clone_file(original, generated):
                shutil.copy2(original, generated)
            generated.chmod(0o644)
        add(identifier, generated.relative_to(build).as_posix(), pin)
    for identifier, key, filename in (
        ("whisper-original-license", "openai-whisper-license", "OpenAI-Whisper-LICENSE"),
        ("u2netp-original-license", "u2netp-license", "U2Net-LICENSE"),
        ("mlx-model-card", "mlx-whisper-card", "mlx-README.md"),
        ("chatterbox-model-card", "chatterbox-model-card", "chatterbox-README.md"),
        ("piper-model-card", "piper-fettah-card", "PIPER_FETTAH_MODEL_CARD"),
        ("piper-voices-declaration", "piper-voices-license-declaration", "PIPER_VOICES_README")):
        add(identifier, "model-cache/" + filename, pins[key])
    # The unchanged Apache-2.0 legal text is reusable; preserve the MLX model's
    # own attribution separately, not a misleading U2Net-named Whisper notice.
    add("apache-2.0-text", "model-cache/U2Net-LICENSE", pins["u2netp-license"])
    chatterbox = read(repository / "config/native-sources.json")["inputs"]["chatterbox-source"]
    source_root = "native-source/chatterbox-pristine/"
    receipt = read(safe_member(build, source_root + "ksi-source-provenance.json"))
    if receipt.get("commit") != chatterbox["commit"] or receipt.get("url") != chatterbox["url"]:
        raise ValueError("Chatterbox notice source does not match the exact Git source override.")
    license = next(row for row in receipt["files"] if row["path"] == "LICENSE")
    original = safe_member(build, source_root + "LICENSE")
    add("chatterbox-original-license", source_root + "LICENSE", dict(license, size=original.stat().st_size))
    # The Argos model grant is its separate maintainer declaration, not inferred
    # from the engine license. Preserve both it and the original project notice.
    lock = read(repository / "config/python-wheels-arm64.json")
    wheel = next(row for row in validate_wheel_lock(lock) if row["name"] == "argostranslate")
    report = read(build / "wheel-notices-main-arm64/wheel-notices.json")
    package = next(row for row in report["packages"] if row["name"] == "argostranslate")
    if package["wheel_sha256"] != wheel["sha256"] or package["version"] != wheel["version"] or package["source_url"] != wheel["url"]:
        raise ValueError("Argos original MIT notice does not match the exact public wheel.")
    license = next(row for row in package["files"] if row["kind"] == "license" and Path(row["path"]).name == "LICENSE")
    add("argos-original-license", "wheel-notices-main-arm64/" + license["path"], license)
    gemma = ["gemma-original-license", "gemma-terms", "gemma-prohibited-use", "gemma-notice", "ksi-model-terms"]
    result = {"schema_version": 1, "files": files, "redistribution_review_complete": False,
        "families": {"translategemma": gemma, "qwen3.5": ["qwen-original-license"],
            "whisper": ["whisper-original-license", "apache-2.0-text", "mlx-model-card"],
            "chatterbox": ["chatterbox-original-license", "chatterbox-model-card"],
            "piper": ["piper-voices-declaration", "piper-model-card"], "u2netp": ["u2netp-original-license"],
            "argos-en-tr": ["argos-original-license", "argos-model-notice"],
            "argos-tr-en": ["argos-original-license", "argos-model-notice"]}}
    atomic_write_json(destination, result, mode=0o644)
    return {"notice_files": len(files), "redistribution_review_complete": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("build", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(json.dumps(build_bindings(Path(__file__).resolve().parents[1], args.build.absolute(), args.destination.absolute())))


if __name__ == "__main__":
    main()
