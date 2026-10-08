import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy

from ksi_local.mlx_audio import decode_packaged_audio


class PackagedMLXAudioTests(unittest.TestCase):
    def test_decoder_uses_exact_verified_tool_and_local_protocol_not_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / "synthetic.wav"
            source.write_bytes(b"synthetic input")
            captured = []
            def decode(command, **kwargs):
                captured.append((command, kwargs))
                numpy.array([0.1, -0.2, 0.0], dtype="<f4").tofile(command[-1])
            with patch("ksi_local.mlx_audio.tool_path", return_value=root / "verified-ffmpeg"), \
                 patch("ksi_local.mlx_audio.run_engine", side_effect=decode), \
                 patch("ksi_local.mlx_audio.validate_internal_path", return_value=root):
                audio = decode_packaged_audio(source, 3.0)
            numpy.testing.assert_array_equal(audio, numpy.array([0.1, -0.2, 0.0], dtype=numpy.float32))
            command, options = captured[0]
            self.assertEqual(command[0], str(root / "verified-ffmpeg"))
            self.assertEqual(command[command.index("-protocol_whitelist") + 1], "file,pipe")
            self.assertEqual(command[command.index("-ar") + 1], "16000")
            self.assertEqual(command[command.index("-ac") + 1], "1")
            self.assertEqual(command[command.index("-t") + 1], "3.0")
            self.assertEqual(int(command[command.index("-fs") + 1]), 4 * 64000)
            self.assertEqual(options["timeout"], 300)
            self.assertFalse(Path(command[-1]).parent.exists())

    def test_unknown_nonfinite_zero_or_excessive_duration_rejects_before_tools(self):
        for duration in (None, float("nan"), float("inf"), 0, -1, 10801):
            with self.subTest(duration=duration), patch("ksi_local.mlx_audio.tool_path") as tool:
                with self.assertRaises(ValueError):
                    decode_packaged_audio(Path("synthetic.wav"), duration)
                tool.assert_not_called()

    def test_external_temporary_root_rejects_before_creation_or_motor(self):
        with patch("ksi_local.mlx_audio.tool_path", return_value="/synthetic/ffmpeg"), \
             patch("ksi_local.mlx_audio.validate_internal_path", side_effect=RuntimeError("external fixture")), \
             patch("ksi_local.mlx_audio.tempfile.TemporaryDirectory") as directory, \
             patch("ksi_local.mlx_audio.run_engine") as engine:
            with self.assertRaisesRegex(RuntimeError, "external fixture"):
                decode_packaged_audio(Path("synthetic.wav"), 3)
            directory.assert_not_called()
            engine.assert_not_called()

    def test_empty_truncated_oversized_nonfinite_or_linked_output_rejects_and_cleans(self):
        for mode in ("empty", "truncated", "oversized", "nonfinite", "linked"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                outputs = []
                def decode(command, **kwargs):
                    output = Path(command[-1])
                    outputs.append(output)
                    if mode == "empty":
                        output.touch()
                    elif mode == "truncated":
                        output.write_bytes(b"abc")
                    elif mode == "oversized":
                        output.write_bytes(b"x" * (int(command[command.index("-fs") + 1]) + 4))
                    elif mode == "nonfinite":
                        numpy.array([float("nan")], dtype="<f4").tofile(output)
                    else:
                        output.symlink_to(root / "unused")
                with patch("ksi_local.mlx_audio.tool_path", return_value="/synthetic/ffmpeg"), \
                     patch("ksi_local.mlx_audio.validate_internal_path", return_value=root), \
                     patch("ksi_local.mlx_audio.run_engine", side_effect=decode):
                    with self.assertRaises(RuntimeError):
                        decode_packaged_audio(root / "synthetic.wav", 0.1)
                self.assertFalse(outputs[0].parent.exists())

    def test_engine_failure_cleans_private_pcm_before_propagating(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            outputs = []
            def fail(command, **kwargs):
                output = Path(command[-1])
                outputs.append(output)
                output.write_bytes(b"partial")
                raise TimeoutError("synthetic motor deadline")
            with patch("ksi_local.mlx_audio.tool_path", return_value="/synthetic/ffmpeg"), \
                 patch("ksi_local.mlx_audio.validate_internal_path", return_value=root), \
                 patch("ksi_local.mlx_audio.run_engine", side_effect=fail):
                with self.assertRaisesRegex(TimeoutError, "deadline"):
                    decode_packaged_audio(root / "synthetic.wav", 1)
            self.assertFalse(outputs[0].parent.exists())
