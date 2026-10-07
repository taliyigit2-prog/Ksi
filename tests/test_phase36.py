from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from ksi_local.release_prep import audit_git_history, audit_public_tree, build_public_tree


ROOT = Path(__file__).resolve().parents[1]


class Phase36Tests(unittest.TestCase):
    def test_clean_public_tree_is_new_audited_source_only_and_reproducible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "ksi-local-studio"
            report = build_public_tree(ROOT, target)
            self.assertTrue(report.passed)
            self.assertGreater(report.file_count, 100)
            self.assertFalse((target / ".git").exists())
            self.assertFalse((target / ".phase1").exists())
            self.assertFalse((target / "dist").exists())
            self.assertFalse((target / "docs/HANDOFF_2026-09-29.md").exists())
            self.assertTrue((target / "src/ksi_local/core_service.py").is_file())
            self.assertTrue((target / "native/KSIOCR.swift").is_file())
            self.assertEqual(audit_public_tree(target), ())
            with self.assertRaises(FileExistsError):
                build_public_tree(ROOT, target)

    def test_public_tree_rejects_a_dangling_symlink_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / "public"
            referent = root / "elsewhere"
            destination.symlink_to(referent)
            with self.assertRaises(FileExistsError):
                build_public_tree(ROOT, destination)
            self.assertFalse(referent.exists())

    def test_every_blob_in_clean_initial_history_is_audited(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "public"
            build_public_tree(ROOT, target)
            commands = (
                ("git", "init", "-b", "main"),
                ("git", "config", "user.name", "KSI Test"),
                ("git", "config", "user.email", "test@localhost"),
                ("git", "add", "--all"),
                ("git", "commit", "-m", "Initial public source"),
            )
            for command in commands:
                subprocess.run(command, cwd=target, check=True, capture_output=True)
            self.assertEqual(audit_public_tree(target), ())
            self.assertEqual(audit_git_history(target), ())

    def test_machine_configuration_is_sanitized_and_private_catalog_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "public"
            build_public_tree(ROOT, target)
            tools = json.loads((target / "config/tool-manifest.json").read_text())
            voice = json.loads((target / "config/voice-profile.json").read_text())
            serialized = json.dumps({"tools": tools, "voice": voice})
            self.assertTrue(tools["public_template"])
            self.assertNotIn("/Users/", serialized)
            self.assertNotIn("user_evaluation", voice)
            self.assertNotIn("environment", voice["runtime"])
            self.assertIsNone(voice["voice"]["reference_audio"])
            self.assertEqual(voice["voice"]["type"], "builtin_synthetic")
            for record in tools["tools"].values():
                self.assertNotIn("installed_on_external_ssd", record)
                if "integrity_verified" in record:
                    self.assertFalse(record["integrity_verified"])
            self.assertFalse((target / "private-catalog.local.json").exists())
            self.assertFalse((target / "workspace-id.json").exists())

    def test_source_manifest_and_spdx_cover_release_without_models(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "public"
            build_public_tree(ROOT, target)
            source_manifest = json.loads((target / "SOURCE-MANIFEST.json").read_text())
            sbom = json.loads((target / "SBOM.spdx.json").read_text())
            paths = {item["path"] for item in source_manifest["files"]}
            self.assertIn("src/ksi_local/gui.py", paths)
            self.assertFalse(any("models" in Path(path).parts for path in paths))
            self.assertEqual(sbom["spdxVersion"], "SPDX-2.3")
            names = {item["name"] for item in sbom["packages"]}
            self.assertIn("ksi-local-studio", names)
            self.assertIn("PySide6_Essentials", names)

    def test_fixture_allowlist_does_not_exempt_other_values_in_same_file(self) -> None:
        from ksi_local.release_prep import _synthetic_test_match

        known = "sk-" + "abcdefghijklmnopqrstuvwxyz123456"
        unexpected = "sk-" + "z" * 40
        self.assertTrue(_synthetic_test_match("tests/test_phase36.py", "openai-key", known))
        self.assertFalse(_synthetic_test_match("tests/test_phase36.py", "openai-key", unexpected))

    def test_audit_rejects_secret_symlink_and_private_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "LICENSE").write_text("x")
            (root / "leak.txt").write_text("token sk-abcdefghijklmnopqrstuvwxyz123456")
            (root / ".env").write_text("PASSWORD=x")
            (root / "linked").symlink_to(root / "leak.txt")
            findings = audit_public_tree(root)
            rules = {item.rule for item in findings}
            self.assertIn("openai-key", rules)
            self.assertIn("blocked-file", rules)
            self.assertIn("symlink", rules)

    def test_public_readme_and_community_templates_cover_release_basics(self) -> None:
        readme = (ROOT / "README.md").read_text()
        self.assertIn("Feature matrix", readme)
        self.assertIn("docs/assets/interface-light.png", readme)
        self.assertIn("docs/assets/workflow.gif", readme)
        self.assertIn("Privacy and legal use", readme)
        for name in ("CONTRIBUTING.md", "CODE_OF_CONDUCT.md", "SECURITY.md", "SUPPORT.md"):
            self.assertTrue((ROOT / name).is_file())
        self.assertTrue((ROOT / ".github/pull_request_template.md").is_file())


if __name__ == "__main__":
    unittest.main()
