"""Synthetic request validation and engine argument contracts."""

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from ksi_local.engine_runner import OperationCancelled
from ksi_local.media_tools import MediaRequest, _validate, process_media


class MediaToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.mp4"
        self.source.write_bytes(b"synthetic input")
        self.output = self.root / "output.mp4"
        self.request = MediaRequest((str(self.source),), str(self.output))
        self.info = {"duration_seconds": 10.0, "video_stream_count": 1, "audio_stream_count": 1,
                     "video_codecs": ["h264"], "audio_codecs": ["aac"], "dimensions": [[640, 480]]}

    def test_never_overwrite_source(self):
        with self.assertRaises(FileExistsError):
            _validate(MediaRequest((str(self.source),), str(self.source)))

    def test_never_overwrite_existing_output(self):
        self.output.write_bytes(b"user output")
        with self.assertRaises(FileExistsError):
            _validate(self.request)

    def test_no_remote_playlist(self):
        playlist = self.root / "source.m3u8"
        playlist.write_text("synthetic playlist")
        with self.assertRaises(ValueError):
            _validate(MediaRequest((str(playlist),), str(self.output)))

    def test_bad_range(self):
        for start, end in ((-1, 5), (4, 3), (float("nan"), 10), (0, float("inf"))):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                _validate(MediaRequest((str(self.source),), str(self.output), "trim", start=start, end=end))

    def test_genuine_lossless_only(self):
        with self.assertRaises(ValueError):
            _validate(MediaRequest((str(self.source),), str(self.output), lossless=True))

    def test_stream_copy_warning_and_atomic_output(self):
        captured = []

        def engine(argv, **kwargs):
            captured.extend(argv)
            Path(argv[-1]).write_bytes(b"synthetic output")

        with patch("ksi_local.media_tools.tool_path", side_effect=lambda name: name), patch(
            "ksi_local.media_tools.probe_local_media", return_value=self.info
        ), patch("ksi_local.media_tools.run_engine", side_effect=engine):
            result = process_media(MediaRequest((str(self.source),), str(self.output), "trim", start=0, end=10, lossless=True))
        self.assertTrue(result.stream_copy)
        self.assertIn("copy", captured)
        self.assertTrue(result.warnings)
        self.assertEqual(self.source.read_bytes(), b"synthetic input")
        self.assertEqual(self.output.read_bytes(), b"synthetic output")

    def test_cancel_does_not_publish(self):
        cancelled = threading.Event()
        cancelled.set()
        with patch("ksi_local.media_tools.tool_path", side_effect=lambda name: name), patch(
            "ksi_local.media_tools.probe_local_media", return_value=self.info
        ), patch("ksi_local.media_tools.run_engine", side_effect=OperationCancelled("cancelled")):
            with self.assertRaises(OperationCancelled):
                process_media(self.request, cancel=cancelled)
        self.assertFalse(self.output.exists())

    def test_incompatible_join_rejected(self):
        second = self.root / "other.mp4"
        second.write_bytes(b"synthetic other")
        with patch("ksi_local.media_tools.tool_path", side_effect=lambda name: name), patch(
            "ksi_local.media_tools.probe_local_media", side_effect=[self.info, dict(self.info, video_codecs=["hevc"])]
        ):
            with self.assertRaises(ValueError):
                process_media(MediaRequest((str(self.source), str(second)), str(self.output), "join", lossless=True))
