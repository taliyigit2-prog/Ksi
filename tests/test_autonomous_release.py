import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ksi_local.autonomous_release import REQUIRED_CASES, REQUIRED_CHECKS, _document, verify_distribution, verify_release
from ksi_local.bundle_runtime import digest_file


class AutonomousReleaseTests(unittest.TestCase):
    def test_every_acceptance_case_has_named_objective_checks(self):
        self.assertEqual(set(REQUIRED_CHECKS), set(REQUIRED_CASES))
        self.assertEqual(len(REQUIRED_CASES), 14)
        self.assertTrue(all(len(checks) >= 2 for checks in REQUIRED_CHECKS.values()))

    def test_evidence_links_and_non_objects_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "data.json"
            original.write_text("[]")
            with self.assertRaises(ValueError):
                _document(original)
            original.write_text("{}")
            linked = root / "linked.json"
            linked.symlink_to(original)
            with self.assertRaises(ValueError):
                _document(linked)

    def test_distribution_hash_changes_close_the_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            part = root / "fixture.dmg"
            part.write_bytes(b"synthetic transport fixture, not a real DMG")
            entry = {"filename": part.name, "size": part.stat().st_size, "sha256": digest_file(part)}
            accepted = {"architecture": "arm64", "offline_manifest_sha256": "1" * 64, "version": "2.0.0"}
            transport = {"schema_version": 1, "product": "KSI Local Studio", "source_commit": "0" * 40,
                         "version": "2.0.0", "architecture": "arm64", "offline_manifest_sha256": "1" * 64,
                         "models_included": True, "notarized": False, "app_signing": "ad-hoc",
                         "transport": "udif", "files": [entry]}
            (root / "transport.json").write_text(json.dumps(transport))
            part.write_bytes(b"changed")
            with patch("ksi_local.autonomous_release.verify_acceptance", return_value=accepted):
                with self.assertRaises(ValueError):
                    verify_distribution(root, root / "fixture.app", root, "0" * 40)

    def test_release_requires_both_native_architectures(self):
        with self.assertRaises(ValueError):
            verify_release({"arm64": {}}, "0" * 40)

    def test_matching_hashes_still_require_native_embedded_app_inspection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            part = root / "fixture.dmg"
            part.write_bytes(b"not installation media")
            accepted = {"architecture": "arm64", "offline_manifest_sha256": "1" * 64, "version": "2.0.0"}
            transport = {"schema_version": 1, "product": "KSI Local Studio", "source_commit": "0" * 40,
                         "version": "2.0.0", "architecture": "arm64", "offline_manifest_sha256": "1" * 64,
                         "models_included": True, "notarized": False, "app_signing": "ad-hoc",
                         "transport": "udif", "files": [{"filename": part.name,
                         "size": part.stat().st_size, "sha256": digest_file(part)}]}
            (root / "transport.json").write_text(json.dumps(transport))
            with patch("ksi_local.autonomous_release.verify_acceptance", return_value=accepted), \
                 patch("ksi_local.autonomous_release.verify_embedded_application", side_effect=ValueError("not a DMG")) as inspect:
                with self.assertRaisesRegex(ValueError, "not a DMG"):
                    verify_distribution(root, root / "KSI Local Studio.app", root, "0" * 40)
                inspect.assert_called_once()


if __name__ == "__main__":
    unittest.main()
