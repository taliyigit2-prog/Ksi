from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QTableWidget

from ksi_local.glossary import load_glossary
from ksi_local.gui import MainWindow, SubtitleReviewDialog
from ksi_local.languages import SUPPORTED_SOURCE_LANGUAGES
from ksi_local.settings import WorkspacePaths
from ksi_local.subtitle_quality import assess_subtitle_quality
from ksi_local.subtitles import Cue, discover_best_subtitle, write_srt
from ksi_local.transcription import filter_speech_segments
from ksi_local.translation import translate_cues


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GLOSSARY_PATH = PROJECT_ROOT / "config" / "glossary.json"


class FakeClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def generate(self, **kwargs: object) -> str:
        self.calls.append(kwargs)
        return self.response


class PhaseFiveCoreTests(unittest.TestCase):
    def test_glossary_covers_all_seven_source_languages(self) -> None:
        self.assertEqual(len(SUPPORTED_SOURCE_LANGUAGES), 7)
        for code in SUPPORTED_SOURCE_LANGUAGES:
            glossary = load_glossary(GLOSSARY_PATH, code)
            self.assertEqual(glossary.source_language, code)
            self.assertGreaterEqual(len(glossary.terms), 3)
            self.assertIn("yapay zekâ", glossary.prompt_fragment())

    def test_ready_subtitle_is_preferred_and_wrong_language_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            valid = root / "source.en-orig.srt"
            invalid = root / "source.fr.srt"
            write_srt(
                valid,
                [Cue(1, "00:00:00,000", "00:00:05,000", "Hello from the video")],
            )
            invalid.write_text("not an srt", encoding="utf-8")
            detection = SimpleNamespace(code="en")
            with patch(
                "ksi_local.language_detection.detect_text_language",
                return_value=detection,
            ):
                automatic = discover_best_subtitle(
                    root, requested_language="auto", stem_prefix="source"
                )
                wrong = discover_best_subtitle(
                    root, requested_language="de", stem_prefix="source"
                )
            self.assertIsNotNone(automatic)
            assert automatic is not None
            self.assertEqual(automatic.path, valid.resolve())
            self.assertEqual(automatic.detected_language, "en")
            self.assertIsNone(wrong)

    def test_silence_and_repetitive_hallucinations_are_filtered(self) -> None:
        segments = [
            {
                "text": "silence",
                "no_speech_prob": 0.95,
                "avg_logprob": -0.9,
                "compression_ratio": 1.0,
            },
            {
                "text": "repeated repeated repeated",
                "no_speech_prob": 0.1,
                "avg_logprob": -1.1,
                "compression_ratio": 3.5,
            },
            {
                "text": "real speech",
                "no_speech_prob": 0.1,
                "avg_logprob": -0.2,
                "compression_ratio": 1.2,
            },
        ]
        kept, dropped = filter_speech_segments(segments)
        self.assertEqual(dropped, 2)
        self.assertEqual([item["text"] for item in kept], ["real speech"])

    def test_quality_report_flags_suspicious_but_structurally_valid_text(self) -> None:
        source = [
            Cue(
                8,
                "00:00:00,000",
                "00:00:01,000",
                "Artificial intelligence is useful",
            )
        ]
        translated = [
            Cue(
                8,
                "00:00:00,000",
                "00:00:01,000",
                "Artificial intelligence is useful",
            )
        ]
        report = assess_subtitle_quality(
            source,
            translated,
            source_language="en",
            glossary=load_glossary(GLOSSARY_PATH, "en"),
        )
        codes = {issue.code for issue in report.issues}
        self.assertTrue(report.passed)
        self.assertIn("possibly_untranslated", codes)
        self.assertIn("glossary_term", codes)
        self.assertIn("fast_reading", codes)

    def test_all_languages_keep_timestamps_and_explicit_direction(self) -> None:
        sources = {
            "en": "Artificial intelligence",
            "ru": "искусственный интеллект",
            "es": "inteligencia artificial",
            "de": "Künstliche Intelligenz",
            "zh": "人工智能",
            "fr": "intelligence artificielle",
            "it": "intelligenza artificiale",
        }
        for code, text in sources.items():
            with self.subTest(language=code):
                cue = Cue(4, "00:00:02,000", "00:00:07,000", text)
                response = json.dumps(
                    {"translations": [{"id": 4, "text": "yapay zekâ"}]},
                    ensure_ascii=False,
                )
                client = FakeClient(response)
                result = translate_cues(
                    [cue],
                    source_language=code,
                    client=client,
                    glossary=load_glossary(GLOSSARY_PATH, code),
                )
                self.assertEqual(result[0].start, cue.start)
                self.assertEqual(result[0].end, cue.end)
                self.assertEqual(result[0].text, "yapay zekâ")
                prompt = str(client.calls[0]["prompt"])
                self.assertIn(f"source_lang_code={code}", prompt)
                self.assertIn("target_lang_code=tr", prompt)


class PhaseFiveReviewDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_review_dialog_shows_source_translation_and_flags(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.en.srt"
            translated = root / "turkce.srt"
            quality = root / "turkce.kalite.json"
            cue = Cue(1, "00:00:00,000", "00:00:05,000", "Hello world")
            write_srt(source, [cue])
            write_srt(translated, [cue.with_text("Merhaba dünya")])
            quality.write_text(
                json.dumps(
                    {
                        "error_count": 0,
                        "warning_count": 1,
                        "issues": [
                            {
                                "cue_index": 1,
                                "level": "warning",
                                "message": "Elle kontrol edin.",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            dialog = SubtitleReviewDialog(
                translated, source_path=source, quality_path=quality
            )
            table = dialog.findChild(QTableWidget)
            labels = [item.text() for item in dialog.findChildren(QLabel)]
            self.assertIsNotNone(table)
            assert table is not None
            self.assertEqual(table.item(0, 1).text(), "Hello world")
            self.assertEqual(table.item(0, 2).text(), "Merhaba dünya")
            self.assertIn("Elle kontrol edin", table.item(0, 3).text())
            self.assertTrue(any("1 uyarı" in label for label in labels))
            dialog.close()

    def test_local_ready_subtitle_skips_whisper_and_queues_quality_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media = root / "lesson.mp4"
            sidecar = root / "lesson.en.srt"
            media.touch()
            write_srt(
                sidecar,
                [
                    Cue(
                        1,
                        "00:00:00,000",
                        "00:00:08,000",
                        "This is a sufficiently clear English subtitle for detection.",
                    )
                ],
            )
            workspace_root = root / "KSI-Workspace"
            workspace = WorkspacePaths(
                root=workspace_root,
                jobs=workspace_root / "jobs",
                outputs=workspace_root / "outputs",
                models_ollama=workspace_root / "models/ollama",
                models_whisper=workspace_root / "models/whisper",
                yt_dlp=workspace_root / "tools/yt-dlp/2026.08.19/yt-dlp",
                deno=workspace_root / "tools/deno/2.9.6/deno",
            )
            workspace.jobs.mkdir(parents=True)
            with (
                patch.dict(
                    os.environ,
                    {"KSI_STATE_DIRECTORY": str(root / "state")},
                ),
                patch("ksi_local.gui.resolve_workspace", return_value=workspace),
                patch(
                    "ksi_local.language_detection.detect_text_language",
                    return_value=SimpleNamespace(code="en"),
                ),
            ):
                window = MainWindow()
                window.workspace = workspace
                window.workspace_initialized = True
                window.want_summary.setChecked(False)
                window._prepare_job(str(media), None, max_height=1080)
                self.assertEqual(
                    [item.stage for item in window.pending],
                    ["translate", "subtitle_mux"],
                )
                command = window.pending[0].argv
                self.assertNotIn("transcribe", command)
                self.assertEqual(command.count("--glossary"), 1)
                self.assertIn("--quality-report", command)
                self.assertEqual(window.source_subtitle, sidecar.resolve())
                window.close()


if __name__ == "__main__":
    unittest.main()
