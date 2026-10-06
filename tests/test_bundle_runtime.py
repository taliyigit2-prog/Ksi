"""Network-free payload validation; all data is synthetic."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ksi_local.bundle_runtime import OfflinePayload, host_architecture, safe_member, tool_path


class BundleRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.payload = self.root / "payload"
        (self.payload / "models").mkdir(parents=True)
        (self.payload / "models/example.bin").write_bytes(b"synthetic model")
        self.entry = {
            "path": "models/example.bin", "role": "model", "identifier": "example",
            "size": 15, "sha256": hashlib.sha256(b"synthetic model").hexdigest(),
        }
        self.write_manifest([self.entry])

    def write_manifest(self, files):
        (self.payload / "offline-manifest.json").write_text(json.dumps({
            "schema_version": 1, "architecture": "arm64", "files": files,
        }))

    def load(self):
        return OfflinePayload.load(self.payload, architecture="arm64")

    def test_architectures(self):
        self.assertEqual(host_architecture("AMD64"), "x86_64")
        self.assertEqual(host_architecture("aarch64"), "arm64")
        with self.assertRaises(RuntimeError):
            host_architecture("unknown")

    def test_wrong_architecture(self):
        with self.assertRaises(RuntimeError):
            OfflinePayload.load(self.payload, architecture="x86_64")

    def test_valid_model(self):
        self.assertEqual(self.load().component("model", "example").read_bytes(), b"synthetic model")

    def test_corruption(self):
        (self.payload / self.entry["path"]).write_bytes(b"changed content")
        with self.assertRaises(RuntimeError):
            self.load().component("model", "example")

    def test_escape_and_symlink(self):
        for invalid in ("../outside", "/outside", "models/../outside", "models\\outside", "models//x"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                safe_member(self.payload, invalid)
        (self.payload / "escape").symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            safe_member(self.payload, "escape/outside")

    def test_duplicate_casefold(self):
        self.write_manifest([self.entry, dict(self.entry, path="Models/example.bin", identifier="other")])
        with self.assertRaises(ValueError):
            self.load()

    def test_install_without_network_and_idempotent(self):
        destination = self.root / "installed"
        self.load().install_models(destination)
        self.load().install_models(destination)
        self.assertEqual((destination / "example.bin").read_bytes(), b"synthetic model")

    def test_preserve_conflicting_user_file(self):
        destination = self.root / "installed"
        destination.mkdir()
        (destination / "example.bin").write_bytes(b"user data")
        with self.assertRaises(FileExistsError):
            self.load().install_models(destination)
        self.assertEqual((destination / "example.bin").read_bytes(), b"user data")

    def test_packaged_missing_tool_never_uses_path(self):
        with patch.dict("os.environ", {"KSI_BUNDLE_ROOT": str(self.payload)}), patch(
            "ksi_local.bundle_runtime.shutil.which", return_value="/development/tool"
        ) as lookup:
            with self.assertRaises(RuntimeError):
                tool_path("ffmpeg")
            lookup.assert_not_called()
