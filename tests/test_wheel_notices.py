import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path

from ksi_local.wheel_notices import collect_wheel_notices


class WheelNoticesTests(unittest.TestCase):
    def fixture(self, root, *, license_text=True):
        filename = "example-1.0-py3-none-any.whl"
        archive = root / filename
        with zipfile.ZipFile(archive, "w") as source:
            source.writestr("example/__init__.py", "raise RuntimeError('never execute')")
            source.writestr("example-1.0.dist-info/METADATA", "Name: example\nVersion: 1.0\nLicense: MIT\n")
            if license_text:
                source.writestr("example-1.0.dist-info/licenses/LICENSE", "Synthetic license fixture")
        row = {"name": "example", "version": "1.0", "filename": filename,
            "url": "https://files.pythonhosted.org/packages/" + filename,
            "size": archive.stat().st_size, "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(), "license": "MIT"}
        return {"schema_version": 1, "architecture": "arm64", "python": "3.12", "wheels": [row]}

    def test_original_license_and_metadata_are_preserved_not_executed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lock = self.fixture(root)
            result = collect_wheel_notices(lock, root, root / "notices")
            self.assertEqual(result["missing_license_texts"], [])
            self.assertFalse(result["redistribution_review_complete"])
            self.assertEqual(len(result["packages"][0]["files"]), 2)
            self.assertFalse((root / "notices/example/example/__init__.py").exists())

    def test_license_label_without_license_text_is_reported_missing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lock = self.fixture(root, license_text=False)
            result = collect_wheel_notices(lock, root, root / "notices")
            self.assertEqual(result["missing_license_texts"], ["example"])

    def test_mutated_wheel_cannot_supply_approved_notices(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lock = self.fixture(root)
            lock["wheels"][0]["sha256"] = "0" * 64
            with self.assertRaises(ValueError):
                collect_wheel_notices(lock, root, root / "notices")
