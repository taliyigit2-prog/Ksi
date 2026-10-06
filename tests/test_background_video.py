"""Short-video limits are enforced before loading any background model."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ksi_local.background_video import process_background_video
from ksi_local.media_tools import MediaRequest


class BackgroundVideoTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "synthetic.mp4"
        self.source.write_bytes(b"unit fixture, not actual video")
        self.request = MediaRequest((str(self.source),), str(self.root / "output.mov"), operation="remove_background_video")

    def test_long_video_is_rejected_before_engine_start(self):
        with patch("ksi_local.background_video.probe_local_media", return_value={"duration_seconds": 31, "video_stream_count": 1}), patch("ksi_local.background_video.run_engine") as engine:
            with self.assertRaises(ValueError):
                process_background_video(self.request)
        engine.assert_not_called()

    def test_audio_only_input_is_rejected_before_model_start(self):
        with patch("ksi_local.background_video.probe_local_media", return_value={"duration_seconds": 3, "video_stream_count": 0}), patch("ksi_local.background_video.run_engine") as engine:
            with self.assertRaises(ValueError):
                process_background_video(self.request)
        engine.assert_not_called()

    def test_audio_container_cannot_claim_transparency(self):
        request = MediaRequest((str(self.source),), str(self.root / "output.mp3"), operation="remove_background_video")
        with self.assertRaises(ValueError):
            process_background_video(request)
