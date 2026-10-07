import hashlib
import json
import runpy
import tempfile
import unittest
from pathlib import Path


repository = Path(__file__).resolve().parents[1]
module = runpy.run_path(str(repository / "scripts/stage_isolated_runtime.py"))
stage, validate_runtime = module["stage"], module["validate_runtime"]


class IsolatedRuntimeStagingTests(unittest.TestCase):
    def fixture(self, root):
        runtime = root / "runtime"
        (runtime / "python/bin").mkdir(parents=True)
        (runtime / "python/lib/python3.12").mkdir(parents=True)
        (runtime / "python/bin/python3.12").write_bytes(b"Synthetic interpreter fixture, not executable")
        (runtime / "python/bin/python3.12").chmod(0o755)
        (runtime / "python/lib/python3.12/LICENSE.txt").write_text("Synthetic Python legal-text fixture")
        (runtime / "python/COPYING").write_text("Synthetic Piper GPL legal-text fixture")
        lock = json.loads((repository / "config/python-piper-wheels-x86_64.json").read_text())
        python = json.loads((repository / "config/runtime-sources.json").read_text())["inputs"]["python-x86_64"]
        provenance = {"schema_version": 1, "architecture": "x86_64", "python_version": python["version"],
            "python_source_sha256": python["sha256"], "wheel_lock_sha256": hashlib.sha256(json.dumps(lock, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
        (runtime / "runtime-provenance.json").write_text(json.dumps(provenance))
        return runtime, lock, python, provenance

    def test_piper_copying_is_not_mislabelled_as_the_python_license(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime, lock, python, _ = self.fixture(root)
            result = stage(runtime, lock, python, {}, "piper", root / "stage")
            self.assertFalse(result["acceptance_tested"])
            spec = json.loads((root / "stage/component-specification.json").read_text())
            rows = {row["identifier"]: row for row in spec["files"]}
            self.assertEqual(rows["piper-python"]["license_file"], "piper-python-original-notice")
            self.assertTrue(rows["piper-python-original-notice"]["path"].endswith("lib/python3.12/LICENSE.txt"))
            self.assertTrue(rows["piper-engine-original-notice"]["path"].endswith("python/COPYING"))

    def test_other_input_revision_and_source_override_are_rejected(self):
        for change in ("python_source_sha256", "wheel_lock_sha256", "architecture", "source_overrides"):
            with self.subTest(field=change), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                runtime, lock, python, provenance = self.fixture(root)
                provenance[change] = [{"commit": "unreviewed"}] if change == "source_overrides" else "changed"
                (runtime / "runtime-provenance.json").write_text(json.dumps(provenance))
                with self.assertRaises(ValueError):
                    stage(runtime, lock, python, {}, "piper", root / "stage")
                self.assertFalse((root / "stage").exists())

    def test_unbound_multilingual_source_api_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as temporary:
            runtime, lock, python, _ = self.fixture(Path(temporary))
            with self.assertRaises(ValueError):
                validate_runtime(runtime, lock, python, {}, "chatterbox")
