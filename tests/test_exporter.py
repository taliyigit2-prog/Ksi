from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ksi_local.exporter import export_artifacts


class ExporterTests(unittest.TestCase):
    def test_copies_allowed_artifacts_to_collision_free_folder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "turkce.srt"
            source.write_text("test", encoding="utf-8")
            first = export_artifacts([source], desktop=root, folder_name="Video: test")
            second = export_artifacts([source], desktop=root, folder_name="Video: test")
            self.assertTrue((first / "turkce.srt").is_file())
            self.assertNotEqual(first, second)

    def test_rejects_unlisted_file_types(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "script.py"
            source.write_text("pass", encoding="utf-8")
            with self.assertRaises(ValueError):
                export_artifacts([source], desktop=root, folder_name="bad")

    def test_allows_dubbing_audio_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "turkce-ses.wav"
            source.write_bytes(b"RIFF-test")
            output = export_artifacts([source], desktop=root, folder_name="dub")
            self.assertEqual((output / source.name).read_bytes(), b"RIFF-test")


if __name__ == "__main__":
    unittest.main()
