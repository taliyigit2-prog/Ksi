import json
import runpy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError


fetch = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/fetch_cargo_root_notices.py"))["fetch"]


class CargoRootNoticeTests(unittest.TestCase):
    def inputs(self):
        return {("example/public", "a" * 40): {"packages": [{"name": "synthetic@1.0.0",
            "source_archive_sha256": "b" * 64, "declared_license": "MIT"}], "paths": {"LICENSE"}}}, [], "source-commit", "lock-digest"

    def test_original_notice_bytes_and_exact_public_source_are_retained(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            response = MagicMock()
            response.__enter__.return_value = response
            response.url = "https://raw.githubusercontent.com/example/public/" + "a" * 40 + "/LICENSE"
            response.read.return_value = b"Synthetic original-notice fixture, not a license grant"
            with patch.dict(fetch.__globals__, {"source_identity": lambda *args: self.inputs(), "urlopen": lambda *args, **kwargs: response}):
                result = fetch(root, root, root, root / "notices")
            self.assertEqual(result["original_notices"], 1)
            self.assertFalse(result["redistribution_review_complete"])
            record = json.loads((root / "notices/cargo-root-source-notices.json").read_text())
            notice = record["groups"][0]["notices"][0]
            self.assertEqual(notice["source_url"], response.url)
            self.assertEqual((root / "notices" / notice["path"]).read_bytes(), response.read.return_value)

    def test_missing_original_text_is_recorded_not_replaced(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            def missing(*args, **kwargs):
                raise HTTPError("https://raw.githubusercontent.com/public", 404, "missing", {}, None)
            with patch.dict(fetch.__globals__, {"source_identity": lambda *args: self.inputs(), "urlopen": missing}):
                result = fetch(root, root, root, root / "notices")
            self.assertEqual(result["groups_without_notices"], 1)
            self.assertEqual(result["original_notices"], 0)

    def test_binary_notice_and_off_host_response_abort_collection(self):
        for wrong_host in (False, True):
            with self.subTest(host=wrong_host), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                response = MagicMock()
                response.__enter__.return_value = response
                response.url = "https://example.org/other" if wrong_host else "https://raw.githubusercontent.com/example/public/" + "a" * 40 + "/LICENSE"
                response.read.return_value = b"not a legal text\x00binary"
                with patch.dict(fetch.__globals__, {"source_identity": lambda *args: self.inputs(), "urlopen": lambda *args, **kwargs: response}):
                    with self.assertRaises(ValueError):
                        fetch(root, root, root, root / "notices")
