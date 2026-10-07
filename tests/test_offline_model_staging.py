import hashlib
import json
import runpy
import tempfile
import unittest
from pathlib import Path


stage = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/stage_offline_models.py"))["stage"]


class OfflineModelStagingTests(unittest.TestCase):
    def fixture(self, root):
        repository = root / "source"
        (repository / "config").mkdir(parents=True)
        (repository / "config/model-sources.json").write_text(json.dumps({"inputs": {}}))
        (repository / "config/ollama-model-sources.json").write_text(json.dumps({"models": []}))
        build = root / "inputs"
        build.mkdir()
        notice = build / "LICENSE"
        notice.write_text("Synthetic original license fixture")
        row = {"input": "LICENSE", "path": "licenses/model.txt", "identifier": "model-license",
            "size": notice.stat().st_size, "sha256": hashlib.sha256(notice.read_bytes()).hexdigest()}
        return repository, build, {"schema_version": 1, "files": [row], "families": {}}, notice

    def test_changed_license_fails_before_destination_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository, build, bindings, notice = self.fixture(root)
            notice.write_text("changed")
            with self.assertRaises(ValueError):
                stage(repository, build, bindings, root / "output", "arm64")
            self.assertFalse((root / "output").exists())

    def test_license_input_cannot_escape_explicit_build_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository, build, bindings, _ = self.fixture(root)
            bindings["files"][0]["input"] = "../outside"
            with self.assertRaises(ValueError):
                stage(repository, build, bindings, root / "output", "arm64")

    def test_license_cannot_replace_model_or_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository, build, bindings, _ = self.fixture(root)
            bindings["files"][0]["path"] = "models/example.bin"
            with self.assertRaises(ValueError):
                stage(repository, build, bindings, root / "output", "arm64")

    def test_existing_destination_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository, build, bindings, _ = self.fixture(root)
            destination = root / "output"
            destination.mkdir()
            with self.assertRaises(FileExistsError):
                stage(repository, build, bindings, destination, "arm64")

    def test_unknown_architecture_is_not_inferred(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository, build, bindings, _ = self.fixture(root)
            with self.assertRaises(ValueError):
                stage(repository, build, bindings, root / "output", "unknown")

    def test_qwen_staging_preserves_original_blobs_and_seals_text_profile(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository, build, bindings, _ = self.fixture(root)
            def input_file(relative, content):
                path = build / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
                return dict(size=len(content), sha256=hashlib.sha256(content).hexdigest(), url="https://example.com/synthetic-public-input", revision="synthetic-fixture")
            sources = {}
            for key, name in (("whisper-cpp-turbo", "ggml-large-v3-turbo.bin"), ("piper-fettah-model", "tr_TR-fettah-medium.onnx"), ("piper-fettah-config", "tr_TR-fettah-medium.onnx.json"), ("piper-fettah-card", "PIPER_FETTAH_MODEL_CARD"), ("piper-voices-license-declaration", "PIPER_VOICES_README")):
                sources[key] = input_file("model-cache/" + name, b"Synthetic non-executable model fixture")
            sources["u2netp-model"] = input_file("model-review/u2netp.onnx", b"Synthetic background model fixture")
            for pair in ("en-tr", "tr-en"):
                key = "argos-" + pair
                pin = input_file("model-review/" + key + ".argosmodel", b"Synthetic original archive fixture")
                pin["url"] = "https://example.com/" + key + ".argosmodel"
                sources[key] = pin
                rows = []
                for name in ("metadata.json", "model/model.bin", "model/shared_vocabulary.txt", "sentencepiece.model", "README.md"):
                    relative = key + "/" + name
                    row = input_file("model-staged-" + key + "/" + relative, b"Synthetic direct inference fixture")
                    rows.append(dict(path=relative, size=row["size"], sha256=row["sha256"]))
                receipt = dict(source_sha256=pin["sha256"], source_url=pin["url"], from_code=pair.split("-")[0], to_code=pair.split("-")[1], files=rows)
                (build / ("model-staged-" + key) / "model-staging.json").write_text(json.dumps(receipt))
            layers = []
            for role in ("config", "model", "projector", "template", "license"):
                content = ("Synthetic Qwen original " + role).encode()
                digest = hashlib.sha256(content).hexdigest()
                pin = input_file("model-ollama/blobs/sha256-" + digest, content)
                layers.append(dict(pin, role=role, mediaType="application/vnd.ollama.image." + role, digest="sha256:" + digest))
            original = dict(schemaVersion=2, config={key: layers[0][key] for key in ("mediaType", "size", "digest")}, layers=[{key: row[key] for key in ("mediaType", "size", "digest")} for row in layers[1:]])
            original_bytes = json.dumps(original).encode()
            original_path = "model-ollama/manifests/registry.ollama.ai/library/qwen3.5/4b"
            manifest_pin = input_file(original_path, original_bytes)
            model = dict(name="qwen3.5", tag="4b", license="Apache-2.0", manifest=manifest_pin, layers=layers)
            (repository / "config/model-sources.json").write_text(json.dumps(dict(inputs=sources)))
            (repository / "config/ollama-model-sources.json").write_text(json.dumps(dict(models=[model])))
            bindings["families"] = {name: ["model-license"] for name in ("qwen3.5", "whisper", "piper", "u2netp", "argos-en-tr", "argos-tr-en")}
            destination = root / "output"
            result = stage(repository, build, bindings, destination, "x86_64")
            self.assertFalse(result["acceptance_tested"])
            self.assertEqual((build / original_path).read_bytes(), original_bytes)
            preserved = destination / "sources/models/qwen3.5/original-registry-manifest.json"
            self.assertEqual(preserved.read_bytes(), original_bytes)
            derived = json.loads((destination / "models/ollama/manifests/registry.ollama.ai/library/qwen3.5/4b").read_text())
            self.assertFalse(any(row["mediaType"].endswith(".projector") for row in derived["layers"]))
            for row in layers:
                blob = destination / ("models/ollama/blobs/sha256-" + row["sha256"])
                self.assertEqual(hashlib.sha256(blob.read_bytes()).hexdigest(), row["sha256"])
            spec = json.loads((destination / "component-specification.json").read_text())
            manifest = next(row for row in spec["files"] if row["identifier"] == "qwen3.5-manifest")
            self.assertEqual(manifest["role"], "model")
            self.assertEqual(manifest["revision"], "qwen-text-profile-v1")
