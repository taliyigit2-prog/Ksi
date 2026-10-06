"""Bundled model inventory with synthetic weights; no network/dependencies."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from ksi_local.bundle_runtime import host_architecture
from ksi_local.model_manager import ModelManager


class ModelManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / "bundle"
        (self.bundle / "models").mkdir(parents=True)
        (self.bundle / "models/example.bin").write_bytes(b"fixture model")
        catalog = {"schema_version": 1, "models": [{"id": "example", "title": "Example",
                   "description": "Synthetic fixture", "license": "MIT", "members": ["weight"]}]}
        (self.bundle / "catalog.json").write_text(json.dumps(catalog))
        files = []
        for path, role, identifier in (("models/example.bin", "model", "weight"), ("catalog.json", "support", "model-catalog")):
            raw = (self.bundle / path).read_bytes()
            files.append({"path": path, "role": role, "identifier": identifier,
                          "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
        (self.bundle / "offline-manifest.json").write_text(json.dumps({"schema_version": 1, "architecture": host_architecture(), "files": files}))
        self.manager = ModelManager(self.bundle, self.root / "installed")

    def test_missing_is_not_installed(self):
        row, = self.manager.inventory()
        self.assertEqual(row.state, "missing")
        self.assertEqual(row.installed_bytes, 0)

    def test_offline_install_then_verify(self):
        row, = self.manager.install()
        self.assertEqual(row.state, "verified")
        self.assertEqual(row.installed_bytes, row.total_bytes)

    def test_same_size_corruption_is_detected(self):
        self.manager.install()
        path = self.root / "installed/example.bin"
        path.write_bytes(b"altered model")
        row, = self.manager.inventory(verify=True)
        self.assertEqual(row.state, "corrupt")

    def test_user_corrupt_file_is_not_silently_overwritten(self):
        self.manager.install()
        path = self.root / "installed/example.bin"
        path.write_bytes(b"altered model")
        with self.assertRaises(FileExistsError):
            self.manager.install()
        self.assertEqual(path.read_bytes(), b"altered model")
