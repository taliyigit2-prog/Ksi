#!/usr/bin/env python3
"""Stage complete architecture-specific models from explicit public input pins.

License bindings are a separate reviewed build input, never inferred from an
installed model cache. This script neither downloads nor approves a release.
"""

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from urllib.parse import urlsplit

from ksi_local.atomic_files import atomic_write_bytes, atomic_write_json
from ksi_local.bundle_runtime import digest_file, safe_member
from ksi_local.copy_on_write import clone_file
from ksi_local.model_staging import ARGOS_INFERENCE_FILES
from ksi_local.ollama_profiles import qwen_text_profile


def read(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 8 * 1024**2:
        raise ValueError("Model build receipt is missing, linked or oversized.")
    return json.loads(path.read_text(encoding="utf-8"))


def stage(repository, build, bindings, destination, architecture):
    if architecture not in {"arm64", "x86_64"} or build.is_symlink() or not build.is_dir():
        raise ValueError("Model staging requires explicit normal public build inputs.")
    if not destination.is_absolute() or destination.exists() or destination.is_symlink():
        raise FileExistsError("Model staging requires a new explicit destination.")
    if bindings.get("schema_version") != 1:
        raise ValueError("Model license binding version is invalid.")
    sources = read(repository / "config/model-sources.json")["inputs"]
    ollama = read(repository / "config/ollama-model-sources.json")["models"]
    rows, plan, models, seen = [], [], [], set()

    def add(origin, target, pin, role, identifier, **metadata):
        safe_member(destination, target)
        if target.casefold() in seen:
            raise ValueError("Model component paths collide.")
        seen.add(target.casefold())
        valid = (len(origin) == pin["size"] and hashlib.sha256(origin).hexdigest() == pin["sha256"]) if isinstance(origin, bytes) else (not origin.is_symlink() and origin.is_file() and origin.stat().st_size == pin["size"] and digest_file(origin) == pin["sha256"])
        if not valid:
            raise ValueError("Model staging input differs from its fixed digest: " + identifier)
        if role == "license" and (isinstance(origin, bytes) or not 0 < pin["size"] <= 256 * 1024 or not origin.read_text(encoding="utf-8").strip()):
            raise ValueError("Model license must be nonempty bounded original text.")
        rows.append(dict(path=target, role=role, identifier=identifier, size=pin["size"], sha256=pin["sha256"], **metadata))
        plan.append((origin, target))
        return identifier

    license_ids = set()
    for row in bindings.get("files", []):
        if row["identifier"] in license_ids or not row["path"].startswith("licenses/"):
            raise ValueError("Model license binding identifier/path is invalid.")
        license_ids.add(row["identifier"])
        add(safe_member(build, row["input"]), row["path"], row, "license", row["identifier"])

    def family(identifier, title, description, license, members):
        notices = bindings.get("families", {}).get(identifier)
        if identifier == "whisper" and architecture == "x86_64" and isinstance(notices, list):
            notices = [notice for notice in notices if notice not in {"apache-2.0-text", "mlx-model-card"}]
        if not isinstance(notices, list) or not notices or not set(notices) <= license_ids:
            raise ValueError("Model family needs explicit original license bindings: " + identifier)
        if license == "Gemma" and not {"gemma-original-license", "gemma-terms", "gemma-prohibited-use", "gemma-notice", "ksi-model-terms"} <= set(notices):
            raise ValueError("Gemma terms, restrictions and Notice are incomplete.")
        result = []
        for origin, target, pin, member_id in members:
            result.append(add(origin, target, pin, "model", member_id, license=license,
                license_file=notices[0], source_url=pin["url"], revision=pin["revision"]))
        models.append(dict(id=identifier, title=title, description=description, license=license,
            members=result, notices=notices))

    for model in ollama:
        members = []
        manifest_name = f"manifests/registry.ollama.ai/library/{model['name']}/{model['tag']}"
        manifest = dict(model["manifest"], revision=model["manifest"]["sha256"])
        manifest_path = safe_member(build / "model-ollama", manifest_name)
        data = read(manifest_path)
        actual = [data["config"], *data["layers"]]
        if len(actual) != len(model["layers"]) or {(row["digest"], row["size"]) for row in actual} != {("sha256:" + row["sha256"], row["size"]) for row in model["layers"]}:
            raise ValueError("Ollama layer closure differs from its complete public pin.")
        if model["name"] == "qwen3.5":
            original = manifest_path.read_bytes()
            derived, binding = qwen_text_profile(original, manifest["sha256"])
            if len(original) != manifest["size"]:
                raise ValueError("Original Qwen manifest size differs from its public pin")
            add(manifest_path, "sources/models/qwen3.5/original-registry-manifest.json", manifest, "support", "qwen-original-registry-manifest")
            own = dict(size=len(derived), sha256=hashlib.sha256(derived).hexdigest(), url="https://github.com/taliyigit2-prog/Ksi", revision="qwen-text-profile-v1")
            members.append((derived, "models/ollama/" + manifest_name, own, model["name"] + "-manifest"))
            proof = (json.dumps(binding, sort_keys=True, separators=(",", ":")) + "\n").encode()
            add(proof, "sources/models/qwen3.5/text-profile-binding.json", dict(size=len(proof), sha256=hashlib.sha256(proof).hexdigest()), "support", "qwen-text-profile-binding")
        else:
            members.append((manifest_path, "models/ollama/" + manifest_name, manifest, model["name"] + "-manifest"))
        for row in model["layers"]:
            name = "blobs/sha256-" + row["sha256"]
            pin = dict(row, url=f"https://registry.ollama.ai/v2/library/{model['name']}/blobs/sha256:{row['sha256']}", revision=model["manifest"]["sha256"])
            members.append((safe_member(build / "model-ollama", name), "models/ollama/" + name, pin, model["name"] + "-" + row["role"]))
        family(model["name"], model["name"] + ":" + model["tag"], "Complete local model package; no first-run download.", model["license"], members)

    def cached(key, cache_name, target, identifier=None):
        return (safe_member(build / "model-cache", cache_name), "models/" + target, sources[key], identifier or key)

    if architecture == "arm64":
        prefix = "whisper/large-v3-turbo-8bit/"
        family("whisper", "Whisper large-v3-turbo · MLX 8-bit", "Apple Silicon local speech recognition.", "Apache-2.0 AND MIT", [
            cached("mlx-whisper-model", "weights.safetensors", prefix + "weights.safetensors"),
            cached("mlx-whisper-config", "mlx-config.json", prefix + "config.json"),
            cached("mlx-whisper-tokenizer", "multilingual.tiktoken", prefix + "multilingual.tiktoken"),
            cached("mlx-whisper-card", "mlx-README.md", prefix + "README.md")])
        names = {"chatterbox-t3-v3": "t3_mtl23ls_v3.safetensors", "chatterbox-s3gen": "s3gen.pt",
            "chatterbox-voice-encoder": "ve.pt", "chatterbox-default-conditions": "conds.pt",
            "chatterbox-v3-tokenizer": "grapheme_mtl_merged_expanded_v1.json", "chatterbox-model-card": "chatterbox-README.md"}
        family("chatterbox", "Chatterbox Multilingual V3", "Local Turkish narrator; upstream public preset, no user reference voice.", "MIT",
            [cached(key, name, "tts/chatterbox-multilingual-v3/" + ("README.md" if key == "chatterbox-model-card" else name)) for key, name in names.items()])
    else:
        family("whisper", "Whisper large-v3-turbo · CPU", "Intel local speech recognition.", "MIT", [cached("whisper-cpp-turbo", "ggml-large-v3-turbo.bin", "whisper/ggml-large-v3-turbo.bin")])
        family("piper", "Piper · Turkish Fettah", "Independent CPU voice preset; not Chatterbox voice cloning.", "MIT", [
            cached("piper-fettah-model", "tr_TR-fettah-medium.onnx", "tts/piper/tr_TR-fettah-medium.onnx", "piper-tr-fettah"),
            cached("piper-fettah-config", "tr_TR-fettah-medium.onnx.json", "tts/piper/tr_TR-fettah-medium.onnx.json", "piper-tr-fettah-config"),
            cached("piper-fettah-card", "PIPER_FETTAH_MODEL_CARD", "tts/piper/MODEL_CARD"),
            cached("piper-voices-license-declaration", "PIPER_VOICES_README", "tts/piper/README.md")])
    family("u2netp", "U2NetP", "Local lightweight image and bounded short-video background removal.", "Apache-2.0", [
        (build / "model-review/u2netp.onnx", "models/rembg/u2netp.onnx", sources["u2netp-model"], "u2netp")])
    for pair in ("en-tr", "tr-en"):
        identifier = "argos-" + pair
        directory = build / ("model-staged-" + identifier)
        receipt = read(directory / "model-staging.json")
        pin = sources[identifier]
        archive = safe_member(build / "model-review", Path(urlsplit(pin["url"]).path).name)
        if not archive.is_file() or archive.stat().st_size != pin["size"] or digest_file(archive) != pin["sha256"]:
            raise ValueError("Argos original archive differs from its official pinned input.")
        if receipt.get("source_sha256") != pin["sha256"] or receipt.get("source_url") != pin["url"] or (receipt.get("from_code"), receipt.get("to_code")) != tuple(pair.split("-")):
            raise ValueError("Argos staging is not bound to the pinned direct language pair.")
        entries = receipt["files"]
        if len(entries) != 5 or {row["path"].split("/", 1)[1] for row in entries} != ARGOS_INFERENCE_FILES or len({row["path"].split("/", 1)[0] for row in entries}) != 1:
            raise ValueError("Argos staging must contain exactly the direct inference files and README.")
        family(identifier, "Argos · " + pair.upper(), "Direct offline English/Turkish translation; original corpus citations retained.", "MIT",
            [(safe_member(directory, row["path"]), "models/argos/" + row["path"], dict(row, url=pin["url"], revision=pin["revision"]),
                identifier + "-" + hashlib.sha256(row["path"].encode()).hexdigest()[:12]) for row in entries])
    destination.mkdir(parents=True, mode=0o700)
    for origin, relative in plan:
        target = safe_member(destination, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(origin, bytes):
            atomic_write_bytes(target, origin, mode=0o644)
        elif not clone_file(origin, target):
            shutil.copy2(origin, target)
        target.chmod(0o644)
    specification = {"schema_version": 1, "architecture": architecture, "files": rows, "models": models}
    atomic_write_json(destination / "component-specification.json", specification, mode=0o644)
    return {"architecture": architecture, "models": len(models), "files": len(rows), "payload_bytes": sum(row["size"] for row in rows), "acceptance_tested": False, "redistribution_review_complete": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("architecture", choices=("arm64", "x86_64"))
    parser.add_argument("build", type=Path)
    parser.add_argument("license_bindings", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[1]
    print(json.dumps(stage(repository, args.build.absolute(), read(args.license_bindings), args.destination.absolute(), args.architecture)))


if __name__ == "__main__":
    main()
