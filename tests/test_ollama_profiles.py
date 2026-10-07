import hashlib
import json
import unittest

from ksi_local.ollama_profiles import qwen_text_profile


class OllamaProfileTests(unittest.TestCase):
    def fixture(self):
        layer = lambda role, digest: dict(mediaType="application/vnd.ollama.image." + role, size=100, digest="sha256:" + digest * 64)
        return json.dumps(dict(schemaVersion=2, config=layer("config", "a"), layers=[layer("model", "b"), layer("projector", "c"), layer("template", "d"), layer("license", "e")])).encode()

    def test_only_projector_reference_is_removed_with_original_weights_unchanged(self):
        original = self.fixture()
        content, binding = qwen_text_profile(original, hashlib.sha256(original).hexdigest())
        before, after = json.loads(original), json.loads(content)
        self.assertEqual(after["config"], before["config"])
        self.assertEqual(after["layers"], [before["layers"][0], *before["layers"][2:]])
        self.assertEqual(binding["model_blob_digest"], before["layers"][0]["digest"])
        self.assertFalse(binding["model_weights_modified"])
        self.assertTrue(binding["original_projector_blob_required"])
        self.assertEqual(binding["derived_manifest_sha256"], hashlib.sha256(content).hexdigest())
        self.assertEqual(qwen_text_profile(original, hashlib.sha256(original).hexdigest())[0], content)

    def test_changed_original_and_missing_or_duplicate_projectors_are_rejected(self):
        original = self.fixture()
        with self.assertRaises(ValueError):
            qwen_text_profile(original + b" ", hashlib.sha256(original).hexdigest())
        for change in ("missing", "duplicate"):
            data = json.loads(original)
            if change == "missing":
                data["layers"].pop(1)
            else:
                data["layers"].append(data["layers"][1])
            altered = json.dumps(data).encode()
            with self.subTest(change=change), self.assertRaises(ValueError):
                qwen_text_profile(altered, hashlib.sha256(altered).hexdigest())

    def test_unpinned_or_boolean_layer_size_is_rejected(self):
        for field, value in (("size", True), ("digest", "unverified")):
            data = json.loads(self.fixture())
            data["layers"][0][field] = value
            original = json.dumps(data).encode()
            with self.subTest(field=field), self.assertRaises(ValueError):
                qwen_text_profile(original, hashlib.sha256(original).hexdigest())
