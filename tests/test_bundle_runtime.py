"""Network-free payload validation; all data is synthetic."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ksi_local.bundle_runtime import OfflinePayload, host_architecture, runtime_payload, safe_member, tool_path


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

    def test_load_still_rejects_linked_manifest_members(self):
        linked = self.payload / "models/example.bin"
        linked.unlink()
        linked.symlink_to(self.root / "outside.bin")
        with self.assertRaises(ValueError):
            self.load()

    def test_component_revalidates_paths_after_manifest_loading(self):
        payload = self.load()
        linked = self.payload / "models/example.bin"
        linked.unlink()
        linked.symlink_to(self.root / "outside.bin")
        with self.assertRaises(ValueError):
            payload.component("model", "example")

    def test_runtime_metadata_cache_never_caches_component_verification(self):
        payload = runtime_payload(self.payload, architecture="arm64")
        self.assertIs(payload, runtime_payload(self.payload, architecture="arm64"))
        (self.payload / self.entry["path"]).write_bytes(b"changed content")
        with self.assertRaises(RuntimeError):
            runtime_payload(self.payload, architecture="arm64").component("model", "example")

    def test_changed_manifest_invalidates_runtime_metadata(self):
        runtime_payload(self.payload, architecture="arm64")
        self.write_manifest([dict(self.entry, sha256="invalid")])
        with self.assertRaises(ValueError):
            runtime_payload(self.payload, architecture="arm64")

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

    def qwen_upgrade_fixture(self):
        from ksi_local.ollama_profiles import qwen_text_profile
        projector = b"synthetic projector"
        projector_sha = hashlib.sha256(projector).hexdigest()
        layer = lambda kind, sha: dict(mediaType="application/vnd.ollama.image." + kind,
                                      size=100, digest="sha256:" + sha)
        original = json.dumps(dict(schemaVersion=2, config=layer("config", "a" * 64),
            layers=[layer("model", "b" * 64), layer("projector", projector_sha)])).encode()
        original_sha = hashlib.sha256(original).hexdigest()
        derived, binding = qwen_text_profile(original, original_sha)
        relative = "models/ollama/manifests/registry.ollama.ai/library/qwen3.5/4b"
        members = [(relative, derived, "model", "qwen3.5-manifest"),
            ("models/ollama/blobs/sha256-" + projector_sha, projector, "model", "projector"),
            ("sources/original.json", original, "support", "qwen-original-registry-manifest"),
            ("sources/binding.json", json.dumps(binding).encode(), "support", "qwen-text-profile-binding")]
        entries = []
        for name, content, role, identifier in members:
            path = self.payload / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            entries.append(dict(path=name, size=len(content), sha256=hashlib.sha256(content).hexdigest(),
                                role=role, identifier=identifier))
        self.write_manifest(entries)
        destination = self.root / "installed"
        target = destination / relative.removeprefix("models/")
        target.parent.mkdir(parents=True)
        target.write_bytes(original)
        backup = destination / f".ksi-model-manifests/qwen3.5-{original_sha}.json"
        return destination, target, backup, original, derived

    def test_known_qwen_manifest_upgrade_preserves_original_and_is_idempotent(self):
        destination, target, backup, original, derived = self.qwen_upgrade_fixture()
        self.load().install_models(destination)
        self.load().install_models(destination)
        self.assertEqual(target.read_bytes(), derived)
        self.assertEqual(backup.read_bytes(), original)

    def test_qwen_upgrade_never_replaces_user_manifest_or_backup(self):
        for changed in ("manifest", "backup", "binding", "backup-link"):
            with self.subTest(changed=changed):
                with tempfile.TemporaryDirectory() as directory:
                    previous = self.root
                    self.root = Path(directory)
                    destination, target, backup, original, _ = self.qwen_upgrade_fixture()
                    if changed == "manifest":
                        target.write_bytes(b"user manifest")
                    elif changed == "binding":
                        (self.payload / "sources/binding.json").write_bytes(b"{}")
                    else:
                        backup.parent.mkdir()
                        if changed == "backup-link":
                            backup.symlink_to(target)
                        else:
                            backup.write_bytes(b"user backup")
                    before = target.read_bytes()
                    with self.assertRaises((FileExistsError, ValueError)):
                        self.load().install_models(destination)
                    self.assertEqual(target.read_bytes(), before)
                    self.root = previous

    def test_linked_install_lock_is_rejected_without_writing_target(self):
        destination = self.root / "installed"
        destination.mkdir()
        (destination / ".ksi-model-install.lock").symlink_to(self.root / "unrelated")
        with self.assertRaises(ValueError):
            self.load().install_models(destination)
        self.assertFalse((self.root / "unrelated").exists())

    def test_packaged_missing_tool_never_uses_path(self):
        with patch.dict("os.environ", {"KSI_BUNDLE_ROOT": str(self.payload)}), patch(
            "ksi_local.bundle_runtime.shutil.which", return_value="/development/tool"
        ) as lookup:
            with self.assertRaises(RuntimeError):
                tool_path("ffmpeg")
            lookup.assert_not_called()
