import hashlib
import io
import runpy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


fetch_source = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/fetch_corresponding_source.py"))["fetch_source"]


class CorrespondingSourceFetchTests(unittest.TestCase):
    def test_digest_is_authoritative_and_existing_cache_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "source.tar.gz"
            content = b"synthetic public source archive"
            expected = hashlib.sha256(content).hexdigest()
            response = io.BytesIO(content)
            response.url = "https://example.com/source.tar.gz"
            opener = MagicMock()
            opener.open.return_value = response
            with patch("urllib.request.build_opener", return_value=opener):
                self.assertEqual(fetch_source(response.url, expected, target, limit=1024), target)
            with patch("urllib.request.build_opener") as network:
                fetch_source(response.url, expected, target, limit=1024)
                network.assert_not_called()
                with self.assertRaises(FileExistsError):
                    fetch_source(response.url, "0" * 64, target, limit=1024)
            self.assertEqual(target.read_bytes(), content)

    def test_wrong_digest_or_size_does_not_publish_or_leave_partial_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for content, limit in ((b"wrong digest", 1024), (b"oversized", 1)):
                response = io.BytesIO(content)
                response.url = "https://example.com/source.tar.gz"
                opener = MagicMock()
                opener.open.return_value = response
                with patch("urllib.request.build_opener", return_value=opener):
                    with self.assertRaises(ValueError):
                        fetch_source(response.url, "0" * 64, root / "source.tar.gz", limit=limit)
                self.assertEqual(list(root.iterdir()), [])
