from __future__ import annotations

import csv
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ksi_local import __version__
from ksi_local.gui import MainWindow
from ksi_local.job_store import JobStatus, JobStore, StageStatus
from ksi_local.review_package import (
    SEGMENT_COLUMNS,
    SUMMARY_COLUMNS,
    apply_review_package,
    create_review_package,
    preview_review_package,
    review_can_undo,
    undo_last_review,
)
from ksi_local.subtitles import Cue, read_srt, write_srt


def _workspace(root: Path):
    from ksi_local.settings import WorkspacePaths

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
    return paths


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rewrite_tsv(path: Path, columns: tuple[str, ...], update: dict[str, str]) -> None:
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, dialect="excel-tab"))
    rows[0].update(update)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, dialect="excel-tab")
        writer.writeheader()
        writer.writerows(rows)


class PhaseEightReviewPackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.job = self.root / "job"
        self.outputs = self.job / "outputs"
        self.outputs.mkdir(parents=True)
        self.source = self.job / "source.en.srt"
        self.translated = self.outputs / "turkce.srt"
        self.quality = self.outputs / "turkce.kalite.json"
        self.summary = self.outputs / "ozet.md"
        self.trace = self.outputs / "ozet.kaynaklar.json"
        self.summary_quality = self.outputs / "ozet.kalite.json"
        self.terms = self.root / "terms.json"
        write_srt(
            self.source,
            [
                Cue(1, "00:00:00,000", "00:00:02,000", "OpenAI saves 42 minutes."),
                Cue(2, "00:00:02,000", "00:00:04,000", "Verify all 99 results."),
            ],
        )
        write_srt(
            self.translated,
            [
                Cue(1, "00:00:00,000", "00:00:02,000", "OpenAI 42 dakika kazandırır."),
                Cue(2, "00:00:02,000", "00:00:04,000", "Sonucu doğrulayın."),
            ],
        )
        self.quality.write_text('{"passed":true}\n', encoding="utf-8")
        self.summary.write_text(
            "# Türkçe Video Özeti\n\n## Kısa özet\n\n"
            "OpenAI 42 dakika kazandırır. [00:00:00]\n\n"
            "## Kaynak ve üretim bilgisi\n\n- Yerel özet\n",
            encoding="utf-8",
        )
        self.trace.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "statements": [
                        {
                            "statement_id": "T0001",
                            "section": "short_summary",
                            "source_ids": ["S000001"],
                            "timestamps": ["00:00:00"],
                            "claim_ids": ["C000001"],
                            "text": "OpenAI 42 dakika kazandırır.",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.summary_quality.write_text('{"passed":true}\n', encoding="utf-8")
        self.terms.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "target_language": "tr",
                    "preserve": ["OpenAI"],
                    "terms": {"en": {}},
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _create(self, *, with_translation: bool = True) -> Path:
        return create_review_package(
            job_id="JOB-8",
            source_language="en",
            source_srt=self.source,
            translated_srt=self.translated if with_translation else None,
            glossary_path=self.terms,
            destination_parent=self.root / "packages",
            summary_path=self.summary,
            summary_trace_path=self.trace,
        )

    def _load_kwargs(self, package: Path, *, with_translation: bool = True) -> dict[str, object]:
        return {
            "package_directory": package,
            "expected_job_id": "JOB-8",
            "source_srt": self.source,
            "translated_srt": self.translated if with_translation else None,
            "glossary_path": self.terms,
            "summary_path": self.summary,
            "summary_trace_path": self.trace,
        }

    def test_package_contains_only_text_evidence_and_hash_manifest(self) -> None:
        package = self._create()
        self.assertEqual(
            {item.name for item in package.iterdir()},
            {
                "manifest.json",
                "segments.jsonl",
                "review.tsv",
                "terms.json",
                "summary.md",
                "summary.trace.json",
                "summary_review.tsv",
                "README_REVIEW.md",
            },
        )
        manifest = json.loads((package / "manifest.json").read_text())
        self.assertEqual(manifest["job_id"], "JOB-8")
        self.assertEqual(manifest["application_version"], __version__)
        self.assertEqual(manifest["source_srt_sha256"], _sha(self.source))
        self.assertEqual(
            manifest["translation"]["machine_translation_sha256"],
            _sha(self.translated),
        )
        self.assertFalse(any(item.suffix in {".mp4", ".wav"} for item in package.iterdir()))

    def test_preview_apply_and_undo_are_hash_bound(self) -> None:
        package = self._create()
        _rewrite_tsv(
            package / "review.tsv",
            SEGMENT_COLUMNS,
            {"corrected_tr": "OpenAI tam 42 dakika kazandırır.", "status": "corrected"},
        )
        _rewrite_tsv(
            package / "summary_review.tsv",
            SUMMARY_COLUMNS,
            {"corrected_tr": "OpenAI tam 42 dakika kazandırır.", "status": "corrected"},
        )
        before = {path.name: _sha(path) for path in self.outputs.iterdir()}
        preview = preview_review_package(**self._load_kwargs(package))
        self.assertEqual(preview.changed_count, 2)
        result = apply_review_package(
            **self._load_kwargs(package),
            job_directory=self.job,
            quality_path=self.quality,
            summary_quality_path=self.summary_quality,
            expected_digest=preview.package_digest,
        )
        self.assertEqual(result.segment_ids, ("S000001",))
        self.assertEqual(result.summary_statement_ids, ("T0001",))
        self.assertEqual(result.dependent_stages, ("tts", "dub_quality", "mux"))
        self.assertIn("tam 42", read_srt(self.translated)[0].text)
        self.assertIn("tam 42", self.summary.read_text(encoding="utf-8"))
        self.assertTrue(review_can_undo(self.job))
        undone = undo_last_review(job_directory=self.job, outputs_directory=self.outputs)
        self.assertTrue(undone.undone)
        self.assertEqual(
            before,
            {path.name: _sha(path) for path in self.outputs.iterdir()},
        )
        self.assertFalse(review_can_undo(self.job))

    def test_summary_only_package_applies_without_turkish_subtitle(self) -> None:
        package = self._create(with_translation=False)
        self.assertFalse((package / "review.tsv").exists())
        _rewrite_tsv(
            package / "summary_review.tsv",
            SUMMARY_COLUMNS,
            {"corrected_tr": "OpenAI yaklaşık 42 dakika kazandırır.", "status": "corrected"},
        )
        preview = preview_review_package(**self._load_kwargs(package, with_translation=False))
        result = apply_review_package(
            **self._load_kwargs(package, with_translation=False),
            job_directory=self.job,
            quality_path=None,
            summary_quality_path=self.summary_quality,
            expected_digest=preview.package_digest,
        )
        self.assertFalse(result.segment_ids)
        self.assertEqual(result.summary_statement_ids, ("T0001",))

    def test_immutable_field_extra_file_and_stale_output_are_rejected(self) -> None:
        package = self._create()
        _rewrite_tsv(package / "review.tsv", SEGMENT_COLUMNS, {"start": "00:00:01,000"})
        with self.assertRaisesRegex(ValueError, "değişmez start"):
            preview_review_package(**self._load_kwargs(package))

        package = self._create()
        (package / "unexpected.txt").write_text("x", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "beklenmeyen"):
            preview_review_package(**self._load_kwargs(package))

        package = self._create()
        write_srt(
            self.translated,
            [Cue(1, "00:00:00,000", "00:00:02,000", "Başka 42 metni."),
             Cue(2, "00:00:02,000", "00:00:04,000", "Sonucu doğrulayın.")],
        )
        with self.assertRaisesRegex(ValueError, "paketten sonra değişmiş"):
            preview_review_package(**self._load_kwargs(package))

    def test_missing_protected_value_is_rejected(self) -> None:
        package = self._create()
        _rewrite_tsv(
            package / "review.tsv",
            SEGMENT_COLUMNS,
            {"corrected_tr": "Bu araç zaman kazandırır.", "status": "corrected"},
        )
        with self.assertRaisesRegex(ValueError, "korunan"):
            preview_review_package(**self._load_kwargs(package))

    def test_missing_and_duplicate_segment_rows_are_rejected(self) -> None:
        package = self._create()
        rows = (package / "review.tsv").read_text(encoding="utf-8").splitlines()
        (package / "review.tsv").write_text("\n".join(rows[:-1]) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "segment sayısı"):
            preview_review_package(**self._load_kwargs(package))

        package = self._create()
        with (package / "review.tsv").open("r", encoding="utf-8", newline="") as handle:
            table = list(csv.DictReader(handle, dialect="excel-tab"))
        table[1]["segment_id"] = table[0]["segment_id"]
        with (package / "review.tsv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=SEGMENT_COLUMNS, dialect="excel-tab")
            writer.writeheader()
            writer.writerows(table)
        with self.assertRaisesRegex(ValueError, "yineleniyor"):
            preview_review_package(**self._load_kwargs(package))

    def test_package_change_after_preview_is_rejected(self) -> None:
        package = self._create()
        _rewrite_tsv(
            package / "review.tsv",
            SEGMENT_COLUMNS,
            {"corrected_tr": "OpenAI tam 42 dakika kazandırır.", "status": "corrected"},
        )
        preview = preview_review_package(**self._load_kwargs(package))
        _rewrite_tsv(package / "review.tsv", SEGMENT_COLUMNS, {"note": "sonradan değişti"})
        with self.assertRaisesRegex(ValueError, "ön izlemesinden sonra"):
            apply_review_package(
                **self._load_kwargs(package),
                job_directory=self.job,
                quality_path=self.quality,
                summary_quality_path=self.summary_quality,
                expected_digest=preview.package_digest,
            )

    def test_undo_rejects_output_changed_after_import(self) -> None:
        package = self._create()
        _rewrite_tsv(
            package / "review.tsv",
            SEGMENT_COLUMNS,
            {"corrected_tr": "OpenAI tam 42 dakika kazandırır.", "status": "corrected"},
        )
        preview = preview_review_package(**self._load_kwargs(package))
        apply_review_package(
            **self._load_kwargs(package),
            job_directory=self.job,
            quality_path=self.quality,
            summary_quality_path=self.summary_quality,
            expected_digest=preview.package_digest,
        )
        self.translated.write_text("elle değiştirildi", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "son düzeltmeden sonra değişmiş"):
            undo_last_review(job_directory=self.job, outputs_directory=self.outputs)


class PhaseEightJobStoreTests(unittest.TestCase):
    def test_completed_dub_stages_can_be_reopened_without_rerunning_translation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = JobStore(root / "jobs.sqlite3")
            source = root / "video.mp4"
            source.write_bytes(b"video")
            store.create_job(
                job_id="job",
                source=str(source),
                source_language="en",
                want_subtitle=True,
                want_summary=False,
                want_dub=True,
                job_directory=root / "job",
            )
            stages = ("translate", "tts", "dub_quality", "mux")
            store.ensure_stages("job", stages)
            for stage in stages:
                store.set_stage("job", stage, StageStatus.RUNNING)
                store.set_stage("job", stage, StageStatus.COMPLETED)
            store.transition_job("job", JobStatus.RUNNING)
            store.transition_job("job", JobStatus.COMPLETED)

            reopened = store.reopen_after_review("job", ("tts", "dub_quality", "mux"))
            self.assertEqual(reopened.status, JobStatus.QUEUED)
            self.assertEqual(store.get_stage("job", "translate").status, StageStatus.COMPLETED)
            for stage in ("tts", "dub_quality", "mux"):
                self.assertEqual(store.get_stage("job", stage).status, StageStatus.PENDING)


class PhaseEightGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_completed_text_job_enables_codex_package_actions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = _workspace(root)
            job = workspace.jobs / "job"
            (job / "source").mkdir(parents=True)
            (job / "outputs").mkdir()
            write_srt(
                job / "source/source.en.srt",
                [Cue(1, "00:00:00,000", "00:00:02,000", "A clear English sentence.")],
            )
            write_srt(
                job / "outputs/turkce.srt",
                [Cue(1, "00:00:00,000", "00:00:02,000", "Açık bir Türkçe cümle.")],
            )
            (job / "outputs/turkce.kalite.json").write_text(
                '{"passed":true}', encoding="utf-8"
            )
            source = root / "lesson.mp4"
            source.write_bytes(b"video")
            with (
                patch.dict(os.environ, {"KSI_STATE_DIRECTORY": str(root / "state")}),
                patch("ksi_local.gui.resolve_workspace", return_value=workspace),
            ):
                window = MainWindow()
                window.workspace = workspace
                window.workspace_initialized = True
                window.store.create_job(
                    job_id="job",
                    source=str(source),
                    source_language="en",
                    want_subtitle=True,
                    want_summary=False,
                    want_dub=False,
                    job_directory=job,
                )
                window.store.transition_job("job", JobStatus.RUNNING)
                window.store.transition_job("job", JobStatus.COMPLETED)
                window._refresh_history()
                window.history.selectRow(0)
                window._history_selection_changed()
                self.assertTrue(window.create_codex_package_button.isEnabled())
                self.assertTrue(window.import_codex_package_button.isEnabled())
                self.assertFalse(window.undo_codex_review_button.isEnabled())
                window.close()


if __name__ == "__main__":
    unittest.main()
