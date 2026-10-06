import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ksi_local.speech_model_integrity import CHATTERBOX_FILES, verify_chatterbox


class SpeechModelIntegrityTests(unittest.TestCase):
    def test_missing_modified_and_linked_weights_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            entries = []
            for name in sorted(CHATTERBOX_FILES):
                content = name.encode("ascii")
                (root / name).write_bytes(content)
                entries.append(SimpleNamespace(role="model", path="models/tts/chatterbox/" + name,
                    sha256=hashlib.sha256(content).hexdigest(), size=len(content)))
            with patch("ksi_local.speech_model_integrity.OfflinePayload.load", return_value=SimpleNamespace(files=entries)):
                identity = verify_chatterbox(root, root)
                self.assertEqual(len(identity), 64)
                target = root / "conds.pt"
                target.write_bytes(b"changed!")
                with self.assertRaises(RuntimeError):
                    verify_chatterbox(root, root)
                target.unlink()
                with self.assertRaises(RuntimeError):
                    verify_chatterbox(root, root)
                target.symlink_to(root / "ve.pt")
                with self.assertRaises(ValueError):
                    verify_chatterbox(root, root)

    def test_incomplete_manifest_and_duplicate_file_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            row = SimpleNamespace(role="model", path="models/tts/conds.pt")
            for entries in ([], [row], [row, row]):
                with self.subTest(count=len(entries)), patch("ksi_local.speech_model_integrity.OfflinePayload.load", return_value=SimpleNamespace(files=entries)):
                    with self.assertRaises((RuntimeError, ValueError)):
                        verify_chatterbox(root, root)
