import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ksi_local.installed_model_integrity import OLLAMA_MANIFESTS, verify_model_tree, verify_ollama_store
from ksi_local.ollama_client import OllamaClient


class InstalledModelIntegrityTests(unittest.TestCase):
    def entry(self, path, content):
        return SimpleNamespace(role="model", path=path, size=len(content), sha256=hashlib.sha256(content).hexdigest())

    def test_whisper_changed_config_fails_before_model_loading(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            entries = []
            for name in ("config.json", "weights.safetensors"):
                content = name.encode()
                (root / name).write_bytes(content)
                entries.append(self.entry("models/whisper/large-v3-turbo-8bit/" + name, content))
            with patch("ksi_local.installed_model_integrity.OfflinePayload.load", return_value=SimpleNamespace(files=entries)):
                verify_model_tree(root, root, prefix="models/whisper/large-v3-turbo-8bit/", required=frozenset({"config.json", "weights.safetensors"}))
                (root / "config.json").write_text("changed")
                with self.assertRaises(RuntimeError):
                    verify_model_tree(root, root, prefix="models/whisper/large-v3-turbo-8bit/")

    def test_ollama_manifests_cannot_reference_unverified_layers(self):
        for missing in (False, True):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                content = b"synthetic layer"
                digest = hashlib.sha256(content).hexdigest()
                blob = "blobs/sha256-" + digest
                (root / "blobs").mkdir()
                (root / blob).write_bytes(content)
                entries = [] if missing else [self.entry("models/ollama/" + blob, content)]
                layer = {"digest": "sha256:" + digest, "size": len(content)}
                for name in OLLAMA_MANIFESTS:
                    manifest = json.dumps({"schemaVersion": 2, "config": layer, "layers": [layer]}).encode()
                    target = root / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(manifest)
                    entries.append(self.entry("models/ollama/" + name, manifest))
                with patch("ksi_local.installed_model_integrity.OfflinePayload.load", return_value=SimpleNamespace(files=entries)):
                    if missing:
                        with self.assertRaises(RuntimeError):
                            verify_ollama_store(root, root)
                    else:
                        verify_ollama_store(root, root)

    def test_packaged_client_does_not_send_prompts_to_unverified_model_tag(self):
        client = OllamaClient()
        with patch("ksi_local.bundle_runtime.bundle_root", return_value=Path("/sealed/resources")), patch.object(OllamaClient, "_post") as transport:
            with self.assertRaises(ValueError):
                client.generate(model="custom:unverified", prompt="synthetic fixture")
            transport.assert_not_called()
