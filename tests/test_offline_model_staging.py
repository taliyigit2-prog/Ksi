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
