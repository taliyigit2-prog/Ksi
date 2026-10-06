import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ksi_local.piper_backend import generate_segments


class PiperBackendTests(unittest.TestCase):
    def test_isolated_cli_and_numeric_timestamp_segment_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cues = [SimpleNamespace(index=3, text="Bir"), SimpleNamespace(index=4, text="İki")]
            def engine(argv, **kwargs):
                self.assertEqual(argv[:4], ["/verified/python", "-I", "-m", "piper"])
                output = Path(argv[argv.index("--output-dir") + 1])
                output.mkdir()
                for name, value in (("9.wav", 1), ("10.wav", 2)):
                    with wave.open(str(output / name), "wb") as audio:
                        audio.setparams((1, 2, 22050, 0, "NONE", "not compressed"))
                        audio.writeframes(bytes([value, 0]) * 2205)
            with patch("ksi_local.piper_backend.verified_voice"), patch("ksi_local.piper_backend.tool_path", return_value="/verified/python"), patch("ksi_local.piper_backend.run_engine", side_effect=engine):
                result = generate_segments(cues, root, root / "models")
            for index, expected in ((3, b"\x01\x00"), (4, b"\x02\x00")):
                with wave.open(str(result[index]), "rb") as audio:
                    self.assertEqual(audio.readframes(1), expected)
