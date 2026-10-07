import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ksi_local import tts_worker


class TtsErrorProtocolTests(unittest.TestCase):
    def test_missing_dependency_and_invalid_upstream_api_return_controlled_events(self):
        for failure in (ModuleNotFoundError("Synthetic missing dependency"), TypeError("Synthetic unavailable upstream class")):
            with self.subTest(error=type(failure).__name__), tempfile.TemporaryDirectory() as directory:
                state = Path(directory) / "state"
                args = ["synthetic.srt", "synthetic.wav", "--segments-directory", str(state / "segments"), "--model-directory", str(state / "models"), "--voice-profile", str(state / "profile.json"), "--ffmpeg", "synthetic-ffmpeg", "--report", str(state / "report.json")]
                output = io.StringIO()
                with patch.dict(os.environ, {"KSI_STATE_DIRECTORY": str(state)}), \
                     patch("ksi_local.tts_worker.signal.signal"), \
                     patch("ksi_local.tts_worker.synthesize", side_effect=failure), \
                     contextlib.redirect_stdout(output):
                    self.assertEqual(tts_worker.main(args), 1)
                event = json.loads(output.getvalue())
                self.assertEqual(event["event"], "error")
                self.assertEqual(event["stage"], "tts")
                self.assertIn("Synthetic", event["message"])
