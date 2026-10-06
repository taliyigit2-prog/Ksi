from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QPlainTextEdit

from ksi_local.downloader import DownloadMode, build_download_plan
from ksi_local.gui import MainWindow, SummaryReviewDialog
from ksi_local.job_store import JobStatus, JobStore
from ksi_local.ollama_client import OllamaClient
from ksi_local.preflight import FormatChoice, PreflightResult
from ksi_local.settings import WorkspacePaths
from ksi_local.subtitles import Cue
from ksi_local.summarization import EvidenceClaim, summarize_cues


class FakeClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    def generate(self, **kwargs: object) -> str:
        self.calls.append(kwargs)
        return self.responses.pop(0)


def two_chunk_cues() -> list[Cue]:
    return [
        Cue(1, "00:00:00,000", "00:00:05,000", "OpenClaw saves two hours."),
        Cue(2, "00:00:10,000", "00:00:15,000", "It automates routine work."),
        Cue(3, "00:05:01,000", "00:05:06,000", "People must verify every result."),
        Cue(4, "00:05:10,000", "00:05:15,000", "Start with a small pilot."),
    ]


FIRST_CLAIMS = json.dumps(
    {
        "claims": [
            {
                "text": "OpenClaw iki saat kazandırabilir.",
                "category": "ana_konu",
                "source_ids": ["S000001"],
            }
        ]
    },
    ensure_ascii=False,
)
SECOND_CLAIMS = json.dumps(
    {
        "claims": [
            {
                "text": "Her sonuç insan tarafından doğrulanmalıdır.",
                "category": "eylem",
                "source_ids": ["S000003"],
            }
        ]
    },
    ensure_ascii=False,
)
FINAL = json.dumps(
    {
        "short_summary": [
            {
                "text": "OpenClaw zaman kazandırır ve insan denetimi gerektirir.",
                "claim_ids": ["C000001", "C000002"],
            }
        ],
        "main_topics": [
            {
                "text": "Rutin işlerin otomasyonu zaman kazandırabilir.",
                "claim_ids": ["C000001"],
            }
        ],
        "important_ideas": [],
        "conclusions": [],
        "actions": [
            {
                "text": "Sonuçları insan denetiminden geçirin.",
                "claim_ids": ["C000002"],
            }
        ],
    },
    ensure_ascii=False,
)


class SummaryPipelineTests(unittest.TestCase):
    def test_every_final_statement_has_source_ids_and_timestamps(self) -> None:
        checkpoints: list[tuple[int, int]] = []
        progress: list[tuple[int, int]] = []
        client = FakeClient([FIRST_CLAIMS, SECOND_CLAIMS, FINAL])
        result = summarize_cues(
            two_chunk_cues(),
            client=client,
            source_title="Demo",
            source_reference="https://youtu.be/demo",
            transcript_sha256="a" * 64,
            target_seconds=300,
            on_checkpoint=lambda done, claims: checkpoints.append((done, len(claims))),
            on_progress=lambda update: progress.append((update.completed, update.total)),
        )
        self.assertEqual(result.chunk_count, 2)
        self.assertEqual(checkpoints, [(1, 1), (2, 2)])
        self.assertEqual(progress[-1], (3, 3))
        self.assertIsInstance(client.calls[0]["json_schema"], dict)
        self.assertEqual(
            client.calls[0]["json_schema"]["properties"]["claims"]["maxItems"],
            6,
        )
        self.assertEqual(
            client.calls[0]["json_schema"]["properties"]["claims"]["items"]
            ["properties"]["source_ids"]["maxItems"],
            12,
        )
        self.assertNotIn("json_mode", client.calls[0])
        self.assertIn('"evidence"', str(client.calls[-1]["prompt"]))
        self.assertIn("[00:00:00]", result.markdown)
        self.assertIn("[00:05:01]", result.markdown)
        self.assertIn("Transkript SHA-256", result.markdown)
        self.assertTrue(result.quality["passed"])
        self.assertEqual(result.quality["source_coverage_percent"], 100.0)
        for statement in result.traceability["statements"]:
            self.assertTrue(statement["source_ids"])
            self.assertTrue(statement["timestamps"])

    def test_invalid_final_references_fall_back_to_validated_claims(self) -> None:
        bad_final = json.dumps(
            {
                "short_summary": [{"text": "Unsupported", "claim_ids": ["C999999"]}],
                "main_topics": [{"text": "Unsupported", "claim_ids": ["C999999"]}],
                "important_ideas": [],
                "conclusions": [],
                "actions": [],
            }
        )
        client = FakeClient([FIRST_CLAIMS, bad_final])
        result = summarize_cues(
            two_chunk_cues()[:2],
            client=client,
            source_title="Demo",
            source_reference="Yerel dosya",
            transcript_sha256="b" * 64,
        )
        self.assertNotIn("Unsupported", result.markdown)
        self.assertIn("OpenClaw iki saat", result.markdown)
        self.assertTrue(result.quality["passed"])

    def test_distinctive_title_name_repairs_close_asr_spelling(self) -> None:
        claims = json.dumps(
            {
                "claims": [
                    {
                        "text": "Open Clause bir bilgisayar ajanıdır.",
                        "category": "ana_konu",
                        "source_ids": ["S000001"],
                    }
                ]
            }
        )
        final = json.dumps(
            {
                "short_summary": ["C000001"],
                "main_topics": ["C000001"],
                "important_ideas": [],
                "conclusions": [],
                "actions": [],
            }
        )
        result = summarize_cues(
            two_chunk_cues()[:2],
            client=FakeClient([claims, final]),
            source_title="OpenClaw tanıtımı",
            source_reference="Yerel dosya",
            transcript_sha256="f" * 64,
        )
        self.assertIn("OpenClaw bir bilgisayar", result.markdown)
        self.assertNotIn("Open Clause", result.markdown)

    def test_exact_title_name_does_not_consume_following_term(self) -> None:
        claims = json.dumps(
            {
                "claims": [
                    {
                        "text": "OpenClaw TUI terminalde açılır.",
                        "category": "eylem",
                        "source_ids": ["S000001"],
                    }
                ]
            }
        )
        final = json.dumps(
            {
                "short_summary": ["C000001"],
                "main_topics": ["C000001"],
                "important_ideas": [],
                "conclusions": [],
                "actions": [],
            }
        )
        result = summarize_cues(
            two_chunk_cues()[:2],
            client=FakeClient([claims, final]),
            source_title="OpenClaw tanıtımı",
            source_reference="Yerel dosya",
            transcript_sha256="1" * 64,
        )
        self.assertIn("OpenClaw TUI terminalde", result.markdown)

    def test_checkpoint_resumes_after_completed_chunk(self) -> None:
        prior = EvidenceClaim(
            "C000001", "OpenClaw iki saat kazandırabilir.", "ana_konu", ("S000001",)
        )
        client = FakeClient([SECOND_CLAIMS, FINAL])
        result = summarize_cues(
            two_chunk_cues(),
            client=client,
            source_title="Demo",
            source_reference="Yerel dosya",
            transcript_sha256="c" * 64,
            target_seconds=300,
            completed_chunks=1,
            initial_claims=[prior],
        )
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(len(result.claims), 2)

    def test_final_selector_can_drop_low_value_claims_and_deduplicates_details(self) -> None:
        selective_final = json.dumps(
            {
                "short_summary": ["C000001"],
                "main_topics": ["C000001"],
                "important_ideas": ["C000001"],
                "conclusions": [],
                "actions": [],
            }
        )
        client = FakeClient([FIRST_CLAIMS, SECOND_CLAIMS, selective_final])
        result = summarize_cues(
            two_chunk_cues(),
            client=client,
            source_title="Demo",
            source_reference="Yerel dosya",
            transcript_sha256="e" * 64,
            target_seconds=300,
        )
        statements = result.traceability["statements"]
        detailed_ids = {
            claim_id
            for statement in statements
            if statement["section"] != "short_summary"
            for claim_id in statement["claim_ids"]
        }
        self.assertEqual(detailed_ids, {"C000001"})
        detailed_occurrences = sum(
            claim_id == "C000001"
            for statement in statements
            if statement["section"] != "short_summary"
            for claim_id in statement["claim_ids"]
        )
        self.assertEqual(detailed_occurrences, 1)
        moments = result.markdown.split("## Zaman kodlu önemli anlar", 1)[1]
        self.assertEqual(moments.count("OpenClaw iki saat"), 1)

    def test_chunk_claim_without_valid_source_is_rejected(self) -> None:
        invalid = json.dumps(
            {
                "claims": [
                    {
                        "text": "Kaynak dışı iddia",
                        "category": "ana_konu",
                        "source_ids": ["S999999"],
                    }
                ]
            }
        )
        with self.assertRaisesRegex(RuntimeError, "ilişkilendirilebilir"):
            summarize_cues(
                two_chunk_cues()[:2],
                client=FakeClient([invalid]),
                source_title="Demo",
                source_reference="Yerel dosya",
                transcript_sha256="d" * 64,
            )


class StructuredOllamaTests(unittest.TestCase):
    def test_json_schema_is_sent_as_generate_format(self) -> None:
        schema: dict[str, object] = {
            "type": "object",
            "properties": {"result": {"type": "string"}},
            "required": ["result"],
        }
        with patch.object(
            OllamaClient, "_post", return_value={"response": '{"result":"ok"}'}
        ) as request:
            response = OllamaClient().generate(
                model="demo",
                prompt="demo",
                json_schema=schema,
                max_tokens=123,
            )
        payload = request.call_args.args[1]
        self.assertEqual(response, '{"result":"ok"}')
        self.assertEqual(payload["format"], schema)
        self.assertEqual(payload["options"]["num_predict"], 123)

    def test_json_mode_and_schema_are_mutually_exclusive(self) -> None:
        with self.assertRaisesRegex(ValueError, "aynı anda"):
            OllamaClient().generate(
                model="demo",
                prompt="demo",
                json_mode=True,
                json_schema={"type": "object"},
            )


class SummaryDownloadTests(unittest.TestCase):
    def test_subtitle_only_mode_skips_video(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan = build_download_plan(
                "https://youtu.be/demo",
                output_directory=directory,
                yt_dlp_path="/tools/yt-dlp",
                subtitle_languages=("en",),
                mode=DownloadMode.SUBTITLES,
            )
        self.assertIn("--skip-download", plan.argv)
        self.assertNotIn("--format", plan.argv)
        self.assertEqual(plan.mode, DownloadMode.SUBTITLES)

    def test_audio_only_mode_has_no_video_merge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan = build_download_plan(
                "https://youtu.be/demo",
                output_directory=directory,
                yt_dlp_path="/tools/yt-dlp",
                subtitle_languages=(),
                mode=DownloadMode.AUDIO,
            )
        self.assertIn("--extract-audio", plan.argv)
        self.assertIn("m4a", plan.argv)
        self.assertNotIn("--merge-output-format", plan.argv)

    def test_non_video_direct_fallback_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "yalnız video"):
                build_download_plan(
                    "https://x.com/example/status/1",
                    output_directory=directory,
                    yt_dlp_path="/tools/yt-dlp",
                    subtitle_languages=("en",),
                    mode=DownloadMode.SUBTITLES,
                    direct_https_only=True,
                )


class ExtendCompletedJobTests(unittest.TestCase):
    def test_completed_summary_job_can_add_subtitle_without_resetting_stages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = JobStore(root / "jobs.sqlite3")
            store.create_job(
                job_id="summary-job",
                source="https://youtu.be/demo",
                source_language="en",
                want_subtitle=False,
                want_summary=True,
                want_dub=False,
                job_directory=root / "job",
            )
            store.ensure_stages("summary-job", ["download", "summarize"])
            store.transition_job("summary-job", JobStatus.RUNNING)
            for stage in ("download", "summarize"):
                store.set_stage("summary-job", stage, "running")
                store.set_stage("summary-job", stage, "completed")
            store.transition_job("summary-job", JobStatus.COMPLETED)
            result = store.extend_job_outputs("summary-job", want_subtitle=True)
            self.assertEqual(result.status, JobStatus.QUEUED)
            self.assertTrue(result.want_summary)
            self.assertTrue(result.want_subtitle)
            self.assertEqual(store.get_stage("summary-job", "summarize").status, "completed")


class PhaseSixGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_summary_dialog_shows_quality_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary = root / "ozet.md"
            quality = root / "ozet.kalite.json"
            summary.write_text("# Özet\n\n[00:00:01] Kanıtlı metin.\n", encoding="utf-8")
            quality.write_text(
                json.dumps(
                    {
                        "passed": True,
                        "statement_count": 4,
                        "source_coverage_percent": 100,
                        "timestamp_coverage_percent": 100,
                    }
                ),
                encoding="utf-8",
            )
            dialog = SummaryReviewDialog(summary, quality_path=quality)
            self.assertIn("Kanıtlı metin", dialog.findChild(QPlainTextEdit).toPlainText())
            labels = [item.text() for item in dialog.findChildren(QLabel)]
            self.assertTrue(any("4 iddia" in label and "%100" in label for label in labels))
            dialog.close()

    def test_online_summary_only_uses_subtitle_download_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
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
            choice = FormatChoice(720, 100, 200, 300, True)
            preflight = PreflightResult(
                source_kind="youtube",
                platform="YouTube",
                title="Demo",
                uploader="Channel",
                duration_seconds=120,
                live_status="not_live",
                entry_count=1,
                manual_subtitles=("en",),
                automatic_subtitles=(),
                detected_language="en",
                requires_authentication=False,
                available_heights=(720,),
                default_height=720,
                format_choices=(choice,),
                fixed_budget=None,
                requested_outputs=("Türkçe özet",),
                free_bytes=1000,
                retry_count=0,
                warnings=(),
                tool_versions=(),
            )
            with (
                patch.dict(
                    os.environ,
                    {"KSI_STATE_DIRECTORY": str(root / "state")},
                ),
                patch("ksi_local.gui.resolve_workspace", return_value=workspace),
            ):
                window = MainWindow()
                window.workspace = workspace
                window.workspace_initialized = True
                window.want_subtitle.setChecked(False)
                window.want_summary.setChecked(True)
                window._prepare_job(
                    "https://youtu.be/demo",
                    None,
                    max_height=720,
                    preflight=preflight,
                )
                command = window.pending[0].argv
                self.assertEqual(command[command.index("--mode") + 1], "subtitles")
                self.assertNotIn("--require-audio", command)
                self.assertIn("--subtitle-language", command)
                window.close()

                no_caption_preflight = PreflightResult(
                    source_kind="youtube",
                    platform="YouTube",
                    title="No captions",
                    uploader="Channel",
                    duration_seconds=120,
                    live_status="not_live",
                    entry_count=1,
                    manual_subtitles=(),
                    automatic_subtitles=(),
                    detected_language="en",
                    requires_authentication=False,
                    available_heights=(720,),
                    default_height=720,
                    format_choices=(choice,),
                    fixed_budget=None,
                    requested_outputs=("Türkçe özet",),
                    free_bytes=1000,
                    retry_count=0,
                    warnings=(),
                    tool_versions=(),
                )
                audio_window = MainWindow()
                audio_window.workspace = workspace
                audio_window.workspace_initialized = True
                audio_window.want_subtitle.setChecked(False)
                audio_window.want_summary.setChecked(True)
                audio_window._prepare_job(
                    "https://youtu.be/no-captions",
                    None,
                    max_height=720,
                    preflight=no_caption_preflight,
                )
                audio_command = audio_window.pending[0].argv
                self.assertEqual(
                    audio_command[audio_command.index("--mode") + 1], "audio"
                )
                self.assertNotIn("--merge-output-format", audio_command)
                audio_window.close()


if __name__ == "__main__":
    unittest.main()
