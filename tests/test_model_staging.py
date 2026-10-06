import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from ksi_local.model_staging import ARGOS_INFERENCE_FILES, stage_argos


class ModelStagingTests(unittest.TestCase):
    def archive(self, root, *, unsafe=False, direction=("en", "tr")):
        path = root / "fixture.argosmodel"
        with zipfile.ZipFile(path, "w") as source:
            for name in ARGOS_INFERENCE_FILES:
                content = json.dumps({"from_code": direction[0], "to_code": direction[1]}) if name == "metadata.json" else "synthetic public fixture"
                source.writestr("package/" + name, content)
            source.writestr("package/stanza/unused.pt", "unused training model")
            if unsafe:
                source.writestr("package/../../escape", "untrusted")
        return path, {"size": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "url": "https://example.com/fixture.argosmodel"}

    def test_inference_subset_preserves_citations_excludes_unused_models(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, entry = self.archive(root)
            destination = root / "staged"
            result = stage_argos(archive, destination, entry, pair=("en", "tr"))
            self.assertEqual(len(result["files"]), 5)
            self.assertTrue((destination / "package/README.md").is_file())
            self.assertFalse((destination / "package/stanza").exists())
            self.assertFalse(result["acceptance_tested"])
            with self.assertRaises(FileExistsError):
                stage_argos(archive, destination, entry, pair=("en", "tr"))

    def test_wrong_direction_and_path_escape_fail_before_creating_destination(self):
        for unsafe, direction in ((True, ("en", "tr")), (False, ("tr", "en"))):
            with self.subTest(unsafe=unsafe), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                archive, entry = self.archive(root, unsafe=unsafe, direction=direction)
                destination = root / "staged"
                with self.assertRaises(ValueError):
                    stage_argos(archive, destination, entry, pair=("en", "tr"))
                self.assertFalse(destination.exists())
