from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from ksi_local.transcription import release_mlx_model, segments_to_cues, transcribe_media


class TranscriptionTests(unittest.TestCase):
    def test_upstream_retained_model_is_released_without_importing_engines(self):
        holder = types.SimpleNamespace(model=object(), model_path="local")
        module = types.SimpleNamespace(ModelHolder=holder)
        calls = []
        core = types.SimpleNamespace(clear_cache=lambda: calls.append("clear"))
        with patch.dict(sys.modules, {"mlx_whisper.transcribe": module, "mlx.core": core}):
            release_mlx_model()
        self.assertIsNone(holder.model)
        self.assertIsNone(holder.model_path)
        self.assertEqual(calls, ["clear"])

    def test_inference_failure_still_releases_retained_model(self):
        holder = types.SimpleNamespace(model=object(), model_path="local")
        whisper = types.ModuleType("mlx_whisper")
        def fail(*args, **kwargs):
            raise RuntimeError("synthetic inference failure")
        whisper.transcribe = fail
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "input.wav"
            source.touch()
            with (patch.dict(sys.modules, {"mlx_whisper": whisper,
                    "mlx_whisper.transcribe": types.SimpleNamespace(ModelHolder=holder)}),
                    patch("ksi_local.transcription.host_architecture", return_value="arm64"),
                    patch("ksi_local.bundle_runtime.bundle_root", return_value=None),
                    patch("ksi_local.transcription.probe_local_media", return_value={} )):
                with self.assertRaisesRegex(RuntimeError, "synthetic inference failure"):
                    transcribe_media(source, Path(temporary) / "output.srt", language="auto", model="local")
        self.assertIsNone(holder.model)
        self.assertIsNone(holder.model_path)

    def test_segments_convert_to_srt_timestamps(self) -> None:
        cues = segments_to_cues(
            [
                {"start": 1.2344, "end": 62.005, "text": " Hello "},
                {"start": 3, "end": 2, "text": "invalid"},
            ]
        )
        self.assertEqual(cues[0].start, "00:00:01,234")
        self.assertEqual(cues[0].end, "00:01:02,005")
        self.assertEqual(cues[0].text, "Hello")

    def test_segments_are_clamped_to_media_duration(self) -> None:
        cues = segments_to_cues(
            [
                {"start": 9.5, "end": 10.2, "text": "last real words"},
                {"start": 10.0, "end": 11.0, "text": "hallucination"},
            ],
            duration_seconds=10.0,
        )
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0].end, "00:00:10,000")

    def test_auto_language_lets_whisper_detect_audio_language(self) -> None:
        captured: dict[str, object] = {}
        fake_whisper = types.ModuleType("mlx_whisper")

        def transcribe(_path: str, **kwargs: object) -> dict[str, object]:
            captured.update(kwargs)
            return {
                "language": "es",
                "segments": [{"start": 0.0, "end": 1.0, "text": "Hola mundo"}],
            }

        fake_whisper.transcribe = transcribe  # type: ignore[attr-defined]
        fake_mlx = types.ModuleType("mlx")
        fake_mlx.__path__ = []  # type: ignore[attr-defined]
        fake_mlx_core = types.ModuleType("mlx.core")
        fake_mlx_core.clear_cache = lambda: None  # type: ignore[attr-defined]
        with tempfile.TemporaryDirectory() as directory:
            media = Path(directory) / "sample.mp4"
            output = Path(directory) / "sample.srt"
            media.touch()
            modules = {
                "mlx_whisper": fake_whisper,
                "mlx": fake_mlx,
                "mlx.core": fake_mlx_core,
            }
            with (
                patch.dict(sys.modules, modules),
                patch("ksi_local.transcription.probe_local_media", return_value={}),
            ):
                result = transcribe_media(media, output, language="auto", model="local")
        self.assertIsNone(captured["language"])
        self.assertEqual(result["detected_language"], "es")


if __name__ == "__main__":
    unittest.main()
