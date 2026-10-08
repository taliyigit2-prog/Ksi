from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ksi_local.dubbing import (
    SegmentMetric,
    atempo_chain,
    build_asr_quality,
    build_timing_quality,
    character_error_rate,
    fit_segment_audio,
    load_voice_profile,
    mux_dubbed_video,
    speed_status,
)
from ksi_local.gui import MainWindow
from ksi_local.preflight import inspect_source
from ksi_local.settings import WorkspacePaths
from ksi_local.subtitles import Cue, write_srt
from ksi_local.subtitle_video import mux_subtitled_video, subtitled_output_path
from ksi_local.tts_worker import _load_checkpoint, _prepare_numba_cache
from ksi_local.transcription import transcribe_media


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VOICE_PROFILE = PROJECT_ROOT / "config/voice-profile.json"


def workspace_at(root: Path) -> WorkspacePaths:
    workspace = root / "KSI-Workspace"
    paths = WorkspacePaths(
        root=workspace,
        jobs=workspace / "jobs",
        outputs=workspace / "outputs",
        models_ollama=workspace / "models/ollama",
        models_whisper=workspace / "models/whisper",
        yt_dlp=workspace / "tools/yt-dlp/2026.08.19/yt-dlp",
        deno=workspace / "tools/deno/2.9.6/deno",
    )
    paths.jobs.mkdir(parents=True)
    paths.outputs.mkdir(parents=True)
    paths.models_ollama.mkdir(parents=True)
    paths.models_whisper.mkdir(parents=True)
    return paths


class PhaseSevenCoreTests(unittest.TestCase):
    def test_numba_cache_is_private_and_rejects_a_symlink_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "numba"
            with patch.dict(
                os.environ,
                {"KSI_NUMBA_CACHE_DIRECTORY": str(cache)},
                clear=False,
            ):
                prepared = _prepare_numba_cache()
                self.assertEqual(prepared, cache.resolve())
                self.assertEqual(os.environ["NUMBA_CACHE_DIR"], str(cache.resolve()))
                self.assertEqual(prepared.stat().st_mode & 0o777, 0o700)
            cache.rmdir()
            cache.symlink_to(root / "redirect", target_is_directory=True)
            with (
                patch.dict(
                    os.environ,
                    {"KSI_NUMBA_CACHE_DIRECTORY": str(cache)},
                    clear=False,
                ),
                self.assertRaisesRegex(ValueError, "sembolik"),
            ):
                _prepare_numba_cache()

    def test_public_voice_template_never_claims_human_acceptance(self) -> None:
        with self.assertRaisesRegex(ValueError, "kabul edilmemiş"):
            load_voice_profile(VOICE_PROFILE)
        payload = json.loads(VOICE_PROFILE.read_text())
        self.assertEqual(payload["status"], "template")
        self.assertIsNone(payload["accepted_at"])
        self.assertEqual(payload["engine"], "chatterbox-multilingual-v3")
        self.assertEqual(payload["voice"]["seed"], 23)
        self.assertEqual(payload["voice"]["language"], "tr")
        self.assertEqual(payload["runtime"]["device"], "mps")

    def test_speed_policy_and_ffmpeg_chain_cover_bounds(self) -> None:
        self.assertEqual(speed_status(1.0), "normal")
        self.assertEqual(speed_status(0.87), "bounded_adjustment")
        self.assertEqual(speed_status(1.4), "needs_rewrite")
        self.assertEqual(atempo_chain(4.0), "atempo=2.00000000,atempo=2.00000000")
        self.assertEqual(atempo_chain(0.25), "atempo=0.50000000,atempo=0.50000000")

    def test_character_error_gate_is_ten_percent(self) -> None:
        target = [Cue(1, "00:00:00,000", "00:00:02,000", "Merhaba dünya")]
        exact = [target[0].with_text("Merhaba dünya")]
        bad = [target[0].with_text("Bambaşka metin")]
        self.assertEqual(character_error_rate("İstanbul!", "istanbul"), 0.0)
        self.assertTrue(build_asr_quality(target, exact)["asr_passed"])
        self.assertFalse(build_asr_quality(target, bad)["asr_passed"])

    def test_checkpoint_keeps_segments_when_only_whole_srt_hash_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkpoint = root / "checkpoint.json"
            checkpoint.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "input_sha256": "old",
                        "profile_sha256": "profile",
                        "model_directory": str(root / "model"),
                        "segments": {"1": {"text_sha256": "kept"}},
                    }
                ),
                encoding="utf-8",
            )
            loaded = _load_checkpoint(
                checkpoint,
                input_sha256="new",
                profile_sha256="profile",
                model_directory=root / "model",
            )
            self.assertEqual(loaded["input_sha256"], "new")
            self.assertIn("1", loaded["segments"])

    def test_timing_quality_enforces_onset_and_peak(self) -> None:
        metric = SegmentMetric(
            index=1,
            start_seconds=0,
            end_seconds=2,
            target_seconds=2,
            generated_seconds=2,
            speed_factor=1,
            leading_silence_seconds=0.6,
            peak_dbfs=-0.2,
            text_sha256="a" * 64,
            audio_sha256="b" * 64,
            status="normal",
        )
        with patch(
            "ksi_local.dubbing.audio_loudness",
            return_value={"integrated_lufs": -16.0, "true_peak_dbfs": -0.2},
        ):
            report = build_timing_quality(
                [metric], output_audio="ignored.wav", ffmpeg_path="ffmpeg"
            )
        self.assertFalse(report["passed"])
        self.assertEqual(report["p95_leading_silence_ms"], 600.0)
        self.assertGreaterEqual(len(report["errors"]), 3)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg yok")
    def test_audio_fit_and_video_mux_produce_verified_media(self) -> None:
        ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
        ffprobe = shutil.which("ffprobe") or "ffprobe"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_audio = root / "long.wav"
            fitted = root / "fitted.wav"
            source_video = root / "source.mp4"
            output = root / "dubbed.mp4"
            subprocess.run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=180:duration=2",
                    str(source_audio),
                ],
                check=True,
            )
            factor = fit_segment_audio(
                source_audio,
                fitted,
                target_seconds=1,
                generated_seconds=2,
                ffmpeg_path=ffmpeg,
            )
            self.assertEqual(factor, 2.0)
            subprocess.run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=black:s=320x180:d=1",
                    "-f",
                    "lavfi",
                    "-i",
                    "anullsrc=r=48000:cl=mono",
                    "-t",
                    "1",
                    "-c:v",
                    "h264",
                    "-c:a",
                    "aac",
                    str(source_video),
                ],
                check=True,
            )
            verification = mux_dubbed_video(
                source_video,
                fitted,
                output,
                ffmpeg_path=ffmpeg,
                ffprobe_path=ffprobe,
            )
            self.assertTrue(output.is_file())
            self.assertGreaterEqual(verification["media"]["video_stream_count"], 1)
            self.assertGreaterEqual(verification["media"]["audio_stream_count"], 1)
            self.assertIsInstance(
                verification["audio_quality"]["integrated_lufs"], float
            )

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg yok")
    def test_translated_subtitle_is_embedded_in_verified_final_video(self) -> None:
        ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
        ffprobe = shutil.which("ffprobe") or "ffprobe"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            subtitle = root / "turkce.srt"
            output = subtitled_output_path(source, root / "outputs")
            write_srt(
                subtitle,
                [Cue(1, "00:00:00,000", "00:00:00,900", "Merhaba dünya")],
            )
            subprocess.run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=black:s=320x180:d=1",
                    "-f",
                    "lavfi",
                    "-i",
                    "anullsrc=r=48000:cl=mono",
                    "-t",
                    "1",
                    "-c:v",
                    "h264",
                    "-c:a",
                    "aac",
                    str(source),
                ],
                check=True,
            )
            verification = mux_subtitled_video(
                source,
                subtitle,
                output,
                ffmpeg_path=ffmpeg,
                ffprobe_path=ffprobe,
            )
            self.assertTrue(output.is_file())
            self.assertEqual(verification["subtitle"]["language"], "tur")
            self.assertTrue(verification["subtitle"]["default"])
            self.assertEqual(verification["subtitle"]["codec"], "mov_text")
            self.assertEqual(list((root / "outputs").glob("*.part.mp4")), [])

    def test_subtitle_only_source_is_rejected_for_dubbing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = workspace_at(root)
            source = root / "lesson.srt"
            write_srt(
                source,
                [Cue(1, "00:00:00,000", "00:00:03,000", "Hello world")],
            )
            with self.assertRaisesRegex(ValueError, "video dosyası"):
                inspect_source(
                    str(source),
                    workspace=workspace,
                    download_only=False,
                    want_subtitle=False,
                    want_summary=False,
                    want_dub=True,
                )

    def test_internal_reasr_accepts_turkish_language(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "voice.wav"
            media.touch()
            fake_whisper = SimpleNamespace(
                transcribe=lambda *_args, **_kwargs: {
                    "language": "tr",
                    "segments": [
                        {
                            "start": 0.0,
                            "end": 1.0,
                            "text": "Merhaba",
                            "no_speech_prob": 0.0,
                            "avg_logprob": -0.1,
                            "compression_ratio": 1.0,
                        }
                    ],
                }
            )
            with (
                patch(
                    "ksi_local.transcription.probe_local_media",
                    return_value={"duration_seconds": 1.0},
                ),
                patch("ksi_local.transcription.host_architecture", return_value="arm64"),
                patch.dict("sys.modules", {"mlx_whisper": fake_whisper}),
            ):
                result = transcribe_media(media, root / "out.srt", language="tr")
            self.assertEqual(result["detected_language"], "tr")


class PhaseSevenGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_dub_only_job_queues_translation_tts_quality_and_mux(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = workspace_at(root)
            (workspace.root / "models/tts/chatterbox-multilingual-v3").mkdir(
                parents=True
            )
            (workspace.root / "models/tts/piper").mkdir(parents=True)
            media = root / "lesson.mp4"
            media.touch()
            write_srt(
                root / "lesson.en.srt",
                [
                    Cue(
                        1,
                        "00:00:00,000",
                        "00:00:04,000",
                        "This is a sufficiently clear English source subtitle.",
                    )
                ],
            )
            with (
                patch.dict(os.environ, {"KSI_STATE_DIRECTORY": str(root / "state")}),
                patch("ksi_local.gui.resolve_workspace", return_value=workspace),
                patch("ksi_local.gui._chatterbox_python_path", return_value=Path(sys.executable)),
                patch(
                    "ksi_local.language_detection.detect_text_language",
                    return_value=SimpleNamespace(code="en"),
                ),
            ):
                window = MainWindow()
                window.workspace = workspace
                window.workspace_initialized = True
                window.want_subtitle.setChecked(False)
                window.want_summary.setChecked(False)
                window.want_dub.setChecked(True)
                window._prepare_job(str(media), None, max_height=1080)
                self.assertEqual(
                    [item.stage for item in window.pending],
                    ["translate", "tts", "dub_quality", "mux"],
                )
                self.assertIn("ksi_local.tts_worker", window.pending[1].argv)
                from ksi_local.bundle_runtime import host_architecture
                expected_engine = "piper" if host_architecture() == "x86_64" else "chatterbox"
                argv = window.pending[1].argv
                self.assertEqual(argv[argv.index("--engine") + 1], expected_engine)
                expected_model = "piper" if expected_engine == "piper" else "chatterbox-multilingual-v3"
                self.assertEqual(argv[argv.index("--model-directory") + 1], str(workspace.root / "models/tts" / expected_model))
                self.assertTrue(window.store.get_job(window.current_job_id).want_dub)
                window.close()

    def test_subtitle_job_queues_a_final_video_after_translation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = workspace_at(root)
            media = root / "lesson.mp4"
            media.touch()
            write_srt(
                root / "lesson.en.srt",
                [Cue(1, "00:00:00,000", "00:00:02,000", "Hello world")],
            )
            with (
                patch.dict(os.environ, {"KSI_STATE_DIRECTORY": str(root / "state")}),
                patch("ksi_local.gui.resolve_workspace", return_value=workspace),
                patch(
                    "ksi_local.language_detection.detect_text_language",
                    return_value=SimpleNamespace(code="en"),
                ),
            ):
                window = MainWindow()
                window.workspace = workspace
                window.workspace_initialized = True
                window.want_subtitle.setChecked(True)
                window.want_summary.setChecked(False)
                window.want_dub.setChecked(False)
                window._prepare_job(str(media), None, max_height=1080)
                self.assertEqual(
                    [item.stage for item in window.pending],
                    ["translate", "subtitle_mux"],
                )
                self.assertIn("mux-subtitle", window.pending[1].argv)
                self.assertTrue(
                    str(window.pending[1].argv[6]).endswith(
                        "outputs/turkce-altyazili.mp4"
                    )
                )
                window.close()


if __name__ == "__main__":
    unittest.main()
