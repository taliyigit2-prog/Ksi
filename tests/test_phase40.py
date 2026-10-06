from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from ksi_local import __version__
from ksi_local.final_release import (
    build_cleanup_preview,
    evaluate_release_gate,
    write_cleanup_preview,
    release_source_sha256,
)
from ksi_local.manual_acceptance import create_session, record_result
from ksi_local.project_metadata import PRODUCT_NAME


class Phase40Tests(unittest.TestCase):
    def _project(self, root: Path) -> Path:
        project = root / "project"
        (project / "src/ksi_local").mkdir(parents=True)
        (project / "pyproject.toml").write_text("[project]\nname='ksi-local-studio'\n")
        return project

    def test_cleanup_preview_is_non_destructive_and_protects_user_data_classes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self._project(Path(directory))
            cache = project / "tests/__pycache__"
            cache.mkdir(parents=True)
            artifact = cache / "test.pyc"
            artifact.write_bytes(b"cache")
            env = project / ".env.local"
            env.write_text("SECRET=must-not-appear")
            old_package = project / "dist/Legacy-1.0.dmg"
            old_package.parent.mkdir()
            old_package.write_bytes(b"old")
            old_pilot = project / "packaging/deployment/phase19_pilot.app"
            old_pilot.mkdir(parents=True)
            (old_pilot / "legacy.bin").write_bytes(b"pilot")
            preview = build_cleanup_preview(project)
            self.assertTrue(artifact.is_file())
            self.assertTrue(env.is_file())
            self.assertFalse(preview.destructive_action_performed)
            self.assertEqual(preview.total_candidate_bytes, 5 + env.stat().st_size + 3 + 5)
            serialized = json.dumps(preview.to_dict(), ensure_ascii=False)
            self.assertNotIn("must-not-appear", serialized)
            self.assertIn("KSI-Workspace", serialized)
            self.assertIn("Kullanıcının kabul ettiği yerel modeller", serialized)
            self.assertIn("dist/Legacy-1.0.dmg", serialized)
            self.assertIn("packaging/deployment/phase19_pilot.app", serialized)

    def test_cleanup_preview_is_atomic_json_and_rejects_wrong_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self._project(root)
            output = root / "preview.json"
            preview = write_cleanup_preview(project, output)
            payload = json.loads(output.read_text())
            self.assertEqual(payload["product"], PRODUCT_NAME)
            self.assertEqual(payload["version"], __version__)
            self.assertEqual(payload["total_candidate_bytes"], preview.total_candidate_bytes)
            with self.assertRaises(ValueError):
                build_cleanup_preview(root / "missing")

    def test_cleanup_preview_does_not_double_count_cache_inside_build(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self._project(Path(directory))
            cache = project / "build/runtime/__pycache__"
            cache.mkdir(parents=True)
            (cache / "module.pyc").write_bytes(b"cache")

            preview = build_cleanup_preview(project)

            self.assertEqual(
                [item.relative_path for item in preview.candidates],
                ["build"],
            )
            self.assertEqual(preview.total_candidate_bytes, 5)

    def test_release_source_fingerprint_covers_native_build_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = self._project(Path(directory))
            native = project / "native"
            native.mkdir()
            helper = native / "KSIOCR.swift"
            helper.write_text("first")
            before = release_source_sha256(project)
            helper.write_text("second")
            self.assertNotEqual(before, release_source_sha256(project))

    def test_release_gate_stays_closed_for_personal_package_and_pending_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self._project(root)
            session = root / "acceptance.json"
            create_session(session)
            package = root / "KSI.dmg"
            package.write_bytes(b"personal")
            manifest = root / "release.json"
            manifest.write_text(json.dumps({
                "product": PRODUCT_NAME,
                "version": __version__,
                "package_filename": package.name,
                "size_bytes": package.stat().st_size,
                "sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
                "source_sha256": release_source_sha256(project),
                "notarized": False,
                "signing": "ad-hoc",
            }))
            gate = evaluate_release_gate(
                acceptance_session=session,
                public_tree_findings=0,
                public_history_findings=0,
                package_path=package,
                package_manifest_path=manifest,
                project_root=project,
            )
            self.assertFalse(gate.ready_to_publish)
            self.assertTrue(gate.checks["package_checksum"])
            self.assertFalse(gate.checks["manual_acceptance"])
            self.assertFalse(gate.checks["developer_id_signature"])
            self.assertFalse(gate.checks["apple_notarization"])
            self.assertTrue(gate.checks["package_matches_current_source"])

    def test_release_gate_can_pass_only_when_every_evidence_gate_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = root / "acceptance.json"
            payload = create_session(session)
            for key, case in payload["cases"].items():
                if not case["blocking"]:
                    continue
                ratings = {name: 5 for name in case["ratings_required"]}
                record_result(session, key, status="passed", ratings=ratings)
            package = root / "KSI.dmg"
            package.write_bytes(b"public")
            manifest = root / "release.json"
            manifest.write_text(json.dumps({
                "product": PRODUCT_NAME,
                "version": __version__,
                "package_filename": package.name,
                "size_bytes": package.stat().st_size,
                "sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
                "source_sha256": release_source_sha256(self._project(root)),
                "notarized": True,
                "signing": "developer-id",
            }))
            gate = evaluate_release_gate(
                acceptance_session=session,
                public_tree_findings=0,
                public_history_findings=0,
                package_path=package,
                package_manifest_path=manifest,
                clean_install_accepted=True,
                cleanup_approved_and_completed=True,
                project_root=root / "project",
                signature_verifier=lambda _path: True,
                notarization_verifier=lambda _path: True,
            )
            self.assertTrue(gate.ready_to_publish)
            self.assertEqual(gate.blockers, ())

    def test_manifest_cannot_forge_signature_or_notarization_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self._project(root)
            session = root / "acceptance.json"
            create_session(session)
            package = root / "unsigned.dmg"
            package.write_bytes(b"not-really-signed")
            manifest = root / "release.json"
            manifest.write_text(json.dumps({
                "product": PRODUCT_NAME,
                "version": __version__,
                "package_filename": package.name,
                "size_bytes": package.stat().st_size,
                "sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
                "source_sha256": release_source_sha256(project),
                "notarized": True,
                "signing": "developer-id",
            }))
            gate = evaluate_release_gate(
                acceptance_session=session,
                public_tree_findings=0,
                public_history_findings=0,
                package_path=package,
                package_manifest_path=manifest,
                project_root=project,
                signature_verifier=lambda _path: False,
                notarization_verifier=lambda _path: False,
            )
            self.assertFalse(gate.checks["developer_id_signature"])
            self.assertFalse(gate.checks["apple_notarization"])


if __name__ == "__main__":
    unittest.main()
