"""Sealing requires pinned content and per-component distribution notices."""

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ksi_local.bundle_runtime import MAX_PAYLOAD_FILES, host_architecture

from ksi_local.offline_build import seal_offline_payload
from ksi_local.model_manager import ModelManager


class OfflineBuildTests(unittest.TestCase):
    def test_catalog_entry_is_reserved_under_the_shared_file_limit(self):
        self.assertEqual(seal_offline_payload.__globals__["MAX_PAYLOAD_FILES"], MAX_PAYLOAD_FILES)
        with patch("ksi_local.offline_build.MAX_PAYLOAD_FILES", len(self.specification["files"])):
            with self.assertRaisesRegex(ValueError, "sayısı"):
                seal_offline_payload(self.resources, self.specification)
        self.assertFalse((self.resources / "offline-manifest.json").exists())
        self.assertFalse((self.resources / "model-catalog.json").exists())

    def test_actual_serialized_manifest_size_is_bounded(self):
        with patch("ksi_local.offline_build.MAX_MANIFEST_BYTES", 1):
            with self.assertRaisesRegex(ValueError, "boyut"):
                seal_offline_payload(self.resources, self.specification)
        self.assertFalse((self.resources / "offline-manifest.json").exists())

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.resources = self.root / "Resources"
        self.resources.mkdir()
        rows = []
        for name, role, identifier, content in (
            ("models/cpu/example.bin", "model", "example", b"synthetic model"),
            ("licenses/example.txt", "license", "example-license", b"MIT fixture"),
        ):
            path = self.resources / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            row = {"path": name, "role": role, "identifier": identifier,
                   "sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}
            if role == "model":
                row.update(license="MIT", license_file="example-license",
                           source_url="https://example.org/model", revision="synthetic-fixture-v1")
            rows.append(row)
        self.specification = {"schema_version": 1, "architecture": host_architecture(), "files": rows,
            "models": [{"id": "cpu", "title": "CPU fixture", "description": "Synthetic test only",
                        "license": "MIT", "members": ["example"]}]}

    def test_sealed_fixture_is_not_claimed_tested(self):
        result = seal_offline_payload(self.resources, self.specification)
        self.assertFalse(result["tested"])
        self.assertEqual(result["files"], 3)
        for name in ("offline-manifest.json", "model-catalog.json"):
            self.assertEqual((self.resources / name).stat().st_mode & 0o777, 0o644)
        self.assertIn("MIT fixture", ModelManager(self.resources, self.root / "installed").license_text("cpu"))
        with self.assertRaises(FileExistsError):
            seal_offline_payload(self.resources, self.specification)

    def test_corrupt_pinned_file_prevents_manifest(self):
        (self.resources / "models/cpu/example.bin").write_bytes(b"changed")
        with self.assertRaises(RuntimeError):
            seal_offline_payload(self.resources, self.specification)
        self.assertFalse((self.resources / "offline-manifest.json").exists())

    def test_model_license_text_is_required(self):
        self.specification["files"][0]["license_file"] = "absent"
        with self.assertRaises(ValueError):
            seal_offline_payload(self.resources, self.specification)

    def test_unknown_license_notice_cannot_reference_arbitrary_local_files(self):
        self.specification["models"][0]["notices"] = ["outside-license"]
        with self.assertRaises(ValueError):
            seal_offline_payload(self.resources, self.specification)

    def test_gemma_cannot_ship_only_a_generic_license_label(self):
        self.specification["models"][0]["license"] = "Gemma"
        with self.assertRaises(ValueError):
            seal_offline_payload(self.resources, self.specification)

    def test_changed_offline_license_does_not_open_unverified_text(self):
        seal_offline_payload(self.resources, self.specification)
        (self.resources / "licenses/example.txt").write_text("different terms")
        manager = ModelManager(self.resources, self.root / "installed")
        with self.assertRaises(RuntimeError):
            manager.license_text("cpu")

    def test_copyleft_engine_requires_corresponding_source(self):
        for license_expression in ("GPL-3.0-only", "LGPL-2.1-or-later", "MIT AND LGPL-3.0-only", "AGPL-3.0-only"):
            with self.subTest(license=license_expression):
                self.specification["files"][0]["license"] = license_expression
                with self.assertRaises(ValueError):
                    seal_offline_payload(self.resources, self.specification)

    def test_all_model_files_must_be_in_the_visible_catalog(self):
        self.specification["models"][0]["members"] = ["unknown"]
        with self.assertRaises(ValueError):
            seal_offline_payload(self.resources, self.specification)

    def test_licensed_shared_library_cannot_bypass_copyleft_source_gate(self):
        path = self.resources / "engines/lib/example.dylib"
        path.parent.mkdir(parents=True)
        content = b"synthetic shared library fixture"
        path.write_bytes(content)
        self.specification["files"].append({"path": "engines/lib/example.dylib",
            "role": "support", "identifier": "example-library", "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(), "license": "LGPL-3.0-only",
            "license_file": "example-license", "source_url": "https://example.org/library",
            "revision": "synthetic-fixture-v1"})
        with self.assertRaisesRegex(ValueError, "kaynak"):
            seal_offline_payload(self.resources, self.specification)
        self.assertFalse((self.resources / "offline-manifest.json").exists())

    def test_licensed_shared_library_requires_original_notice(self):
        path = self.resources / "engines/lib/example.dylib"
        path.parent.mkdir(parents=True)
        content = b"synthetic shared library fixture"
        path.write_bytes(content)
        self.specification["files"].append({"path": "engines/lib/example.dylib",
            "role": "support", "identifier": "example-library", "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(), "license": "MIT",
            "license_file": "missing-notice", "source_url": "https://example.org/library",
            "revision": "synthetic-fixture-v1"})
        with self.assertRaisesRegex(ValueError, "lisans"):
            seal_offline_payload(self.resources, self.specification)
