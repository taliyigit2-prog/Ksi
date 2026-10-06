from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from docx import Document
from pypdf import PdfReader
from PySide6.QtWidgets import QApplication, QLabel

from ksi_local.document_summarization import _parse_claims, split_document_blocks, summarize_document
from ksi_local.gui import MainWindow, SummaryReviewDialog
from ksi_local.job_store import JobKind, JobStatus
from ksi_local.settings import WorkspacePaths


TEST_APP = QApplication.instance() or QApplication([])


def write_canonical(path: Path, texts: list[str]) -> Path:
    records = []
    for sequence, text in enumerate(texts, start=1):
        records.append(
            {
                "schema_version": 1,
                "id": f"B{sequence:06d}",
                "sequence": sequence,
                "source_sha256": "a" * 64,
                "source_format": "txt",
                "block_type": "paragraph",
                "text": text,
                "location": {"page": sequence, "paragraph": 1},
                "extraction_method": "test",
                "detected_language": "tr",
                "language_confidence": 1.0,
                "ocr_confidence": None,
                "style": {},
                "flags": [],
            }
        )
    path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n",
        encoding="utf-8",
    )
    return path


def write_translation(canonical: Path, output: Path, *, passed: bool = True) -> tuple[Path, Path]:
    records = []
    for source in [json.loads(line) for line in canonical.read_text().splitlines()]:
        source_text = source["text"]
        target = "Türkçe çeviri: " + source_text
        records.append(
            {
                "schema_version": 1,
                "id": source["id"],
                "sequence": source["sequence"],
                "source_document_sha256": source["source_sha256"],
                "source_block_sha256": hashlib.sha256(source_text.encode()).hexdigest(),
                "translation_sha256": hashlib.sha256(target.encode()).hexdigest(),
                "source_language": "en",
                "block_type": source["block_type"],
                "location": source["location"],
                "style": {},
                "source_text": source_text,
                "translated_text": target,
                "status": "translate",
                "flags": [],
            }
        )
    translation = output / "belge-turkce.jsonl"
    quality = output / "belge-turkce.kalite.json"
    translation.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n"
    )
    quality.write_text(json.dumps({"schema_version": 1, "passed": passed}))
    return translation, quality


class SummaryClient:
    def __init__(
        self,
        *,
        fail_after: int | None = None,
        bad_evidence: bool = False,
        bad_final: bool = False,
    ) -> None:
        self.calls: list[dict[str, object]] = []
        self.fail_after = fail_after
        self.bad_evidence = bad_evidence
        self.bad_final = bad_final

    def generate(self, **kwargs: object) -> str:
        self.calls.append(kwargs)
        if self.fail_after is not None and len(self.calls) > self.fail_after:
            raise RuntimeError("planned interruption")
        prompt = str(kwargs["prompt"])
        if "\nİDDİALAR:\n" in prompt:
            claims = json.loads(prompt.rsplit("\nİDDİALAR:\n", 1)[1])
            ids = [item["id"] for item in claims]
            if self.bad_final:
                ids = ["C999999"]
            return json.dumps(
                {
                    "short_summary": ids[:2],
                    "main_idea": ids[:1],
                    "important_points": ids[1:3],
                    "conclusions": [],
                    "actions": [],
                },
                ensure_ascii=False,
            )
        blocks = json.loads(prompt.rsplit("\nINPUT:\n", 1)[1])
        claims = []
        for index, block in enumerate(blocks):
            quote = str(block["text"])[:80]
            if self.bad_evidence:
                quote = "Bu alıntı kaynakta bulunmuyor."
            claims.append(
                {
                    "text": f"{block['id']} için doğrulanabilir Türkçe iddia.",
                    "category": "ana_fikir" if index == 0 else "önemli_nokta",
                    "block_ids": [block["id"]],
                    "evidence": [{"block_id": block["id"], "quote": quote}],
                }
            )
        return json.dumps({"claims": claims}, ensure_ascii=False)


class DocumentSummaryCoreTests(unittest.TestCase):
    def test_chunks_are_bounded_by_block_count_for_document_wide_coverage(self) -> None:
        blocks = [
            {"id": f"B{index:06d}", "text": f"Kısa blok {index}."}
            for index in range(1, 18)
        ]
        self.assertEqual([len(chunk) for chunk in split_document_blocks(blocks)], [8, 8, 1])

    def test_unsupported_event_is_grounded_to_exact_label_evidence(self) -> None:
        blocks = [{"id": "B000001", "text": "Belge tarihi: 18 Nisan 2026"}]
        raw = json.dumps(
            {
                "claims": [{
                    "text": "Belge 18 Nisan 2026 tarihinde oluşturuldu.",
                    "category": "ana_fikir",
                    "block_ids": ["B000001"],
                    "evidence": [{
                        "block_id": "B000001",
                        "quote": "Belge tarihi: 18 Nisan 2026",
                    }],
                }]
            },
            ensure_ascii=False,
        )
        claims = _parse_claims(raw, blocks=blocks, first_number=1, limit=1, minimum=1)
        self.assertEqual(claims[0].text, "Belge tarihi: 18 Nisan 2026")

    def test_negated_number_omission_is_grounded_to_complete_evidence(self) -> None:
        blocks = [{"id": "B000001", "text": "Basınç limiti 4.8 bar'dır; 8.4 bar değildir."}]
        raw = json.dumps(
            {
                "claims": [{
                    "text": "Basınç limiti 4.8 bar'dır.",
                    "category": "sonuç",
                    "block_ids": ["B000001"],
                    "evidence": [{
                        "block_id": "B000001",
                        "quote": "Basınç limiti 4.8 bar'dır; 8.4 bar değildir.",
                    }],
                }]
            },
            ensure_ascii=False,
        )
        claims = _parse_claims(raw, blocks=blocks, first_number=1, limit=1, minimum=1)
        self.assertEqual(claims[0].text, "Basınç limiti 4.8 bar'dır; 8.4 bar değildir.")

    def test_profiles_produce_traceable_markdown_and_docx(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical = write_canonical(
                root / "source.jsonl",
                [
                    "Bu belge yerel özet sisteminin ana amacını açıklar.",
                    "Her iddia özgün belge bloğuna ve konumuna bağlanmalıdır.",
                    "İşlem bittikten sonra model bilgisayar belleğinden çıkarılır.",
                ],
            )
            for profile in ("short", "standard", "detailed"):
                case = root / profile
                result = summarize_document(
                    canonical,
                    case,
                    client=SummaryClient(),
                    profile=profile,
                    source_title="Yerel Belge",
                )
                self.assertTrue(result.quality["passed"])
                self.assertEqual(result.quality["source_coverage_percent"], 100.0)
                self.assertEqual(result.quality["evidence_coverage_percent"], 100.0)
                self.assertIn("[B000001 · s. 1", result.markdown_path.read_text())
                self.assertGreater(len(Document(result.docx_path).paragraphs), 3)
                trace = json.loads(result.trace_path.read_text())
                self.assertTrue(
                    all(item["source_block_ids"] for item in trace["statements"])
                )

    def test_optional_pdf_is_openable_and_contains_pages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical = write_canonical(
                root / "source.jsonl",
                ["Türkçe PDF özeti yerel olarak oluşturulmalı ve açılabilmelidir."],
            )
            result = summarize_document(
                canonical,
                root / "out",
                client=SummaryClient(),
                create_pdf=True,
            )
            self.assertIsNotNone(result.pdf_path)
            self.assertGreaterEqual(len(PdfReader(result.pdf_path).pages), 1)

    def test_auto_prefers_verified_translation_and_source_can_be_forced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical = write_canonical(
                root / "source.jsonl",
                ["This foreign document explains the local summary system."],
            )
            translation, quality = write_translation(canonical, root)
            automatic = summarize_document(
                canonical,
                root / "auto",
                client=SummaryClient(),
                translation_path=translation,
                translation_quality_path=quality,
            )
            self.assertEqual(automatic.source_mode, "translation")
            forced = summarize_document(
                canonical,
                root / "source",
                client=SummaryClient(),
                source_mode="source",
                translation_path=translation,
                translation_quality_path=quality,
            )
            self.assertEqual(forced.source_mode, "source")

    def test_unverified_or_mutated_translation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical = write_canonical(root / "source.jsonl", ["Foreign source text."])
            translation, quality = write_translation(canonical, root, passed=False)
            with self.assertRaisesRegex(ValueError, "kalite raporunun"):
                summarize_document(
                    canonical,
                    root / "out",
                    client=SummaryClient(),
                    source_mode="translation",
                    translation_path=translation,
                    translation_quality_path=quality,
                )

    def test_unknown_quote_is_rejected_after_bounded_retries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical = write_canonical(
                root / "source.jsonl", ["Kaynakta doğrulanabilir bir cümle bulunur."]
            )
            client = SummaryClient(bad_evidence=True)
            with self.assertRaisesRegex(RuntimeError, "birebir bulunamadı"):
                summarize_document(canonical, root / "out", client=client)
            self.assertEqual(len(client.calls), 3)
            self.assertFalse((root / "out/belge-ozeti.md").exists())

    def test_reformatted_quote_is_repaired_to_verbatim_source_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical = write_canonical(
                root / "source.jsonl",
                [
                    "Pilot projesinin bütçesi EUR 48,750'dur; EUR 84,750 değildir.",
                    "Yerel işlem sırasında müşteri sesi Mac'ten çıkmaz.",
                    "Elena Petrova sonuçları 30 Haziran 2026 tarihinde inceleyecektir.",
                ],
            )

            class ReformattedQuoteClient(SummaryClient):
                def generate(self, **kwargs: object) -> str:
                    prompt = str(kwargs["prompt"])
                    if "\nİDDİALAR:\n" in prompt:
                        return super().generate(**kwargs)
                    blocks = json.loads(prompt.rsplit("\nINPUT:\n", 1)[1])
                    claims = []
                    for index, block in enumerate(blocks):
                        quote = str(block["text"])
                        if index == 0:
                            quote = "Pilot projesinin bütçesi 48,750 EUR'dur; 84,750 EUR değildir."
                        claims.append(
                            {
                                "text": f"{block['id']} için doğrulanabilir Türkçe iddia.",
                                "category": "ana_fikir" if index == 0 else "önemli_nokta",
                                "block_ids": [block["id"]],
                                "evidence": [{"block_id": block["id"], "quote": quote}],
                            }
                        )
                    return json.dumps({"claims": claims}, ensure_ascii=False)

            result = summarize_document(
                canonical, root / "out", client=ReformattedQuoteClient()
            )
            trace = json.loads(result.trace_path.read_text())
            quotes = {
                evidence["quote"]
                for statement in trace["statements"]
                for evidence in statement["evidence"]
            }
            self.assertIn(
                "Pilot projesinin bütçesi EUR 48,750'dur; EUR 84,750 değildir.",
                quotes,
            )

    def test_checkpoint_resumes_only_verified_completed_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            texts = [
                (f"{index}. blok güvenli devam testi için yeterli metin içerir. " * 30)
                for index in range(1, 4)
            ]
            canonical = write_canonical(root / "source.jsonl", texts)
            checkpoint = root / "summary.checkpoint.json"
            with self.assertRaisesRegex(RuntimeError, "planned interruption"):
                summarize_document(
                    canonical,
                    root / "out",
                    client=SummaryClient(fail_after=1),
                    max_chars=1000,
                    checkpoint_path=checkpoint,
                )
            saved = json.loads(checkpoint.read_text())
            self.assertEqual(saved["completed_chunks"], 1)
            corrupted = json.loads(json.dumps(saved))
            corrupted["claims"][0]["evidence"][0]["quote"] = "kaynakta yok"
            checkpoint.write_text(json.dumps(corrupted))
            with self.assertRaisesRegex(ValueError, "kaynak kimlikleri"):
                summarize_document(
                    canonical,
                    root / "out",
                    client=SummaryClient(),
                    max_chars=1000,
                    checkpoint_path=checkpoint,
                )
            checkpoint.write_text(json.dumps(saved))
            resumed = SummaryClient()
            result = summarize_document(
                canonical,
                root / "out",
                client=resumed,
                max_chars=1000,
                checkpoint_path=checkpoint,
            )
            self.assertEqual(len(resumed.calls), 3)
            self.assertTrue(json.loads(result.checkpoint_path.read_text())["complete"])

    def test_unknown_final_claim_falls_back_without_publishing_unknown_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical = write_canonical(
                root / "source.jsonl", ["Bu belge güvenli final seçimini açıklar."]
            )
            result = summarize_document(
                canonical, root / "out", client=SummaryClient(bad_final=True)
            )
            self.assertNotIn("C999999", result.markdown_path.read_text())
            self.assertTrue(result.quality["passed"])


def workspace_at(root: Path) -> WorkspacePaths:
    workspace = root / "KSI-Workspace"
    paths = WorkspacePaths(
        root=workspace,
        jobs=workspace / "jobs",
        outputs=workspace / "outputs",
        models_ollama=workspace / "models/ollama",
        models_whisper=workspace / "models/whisper",
        yt_dlp=workspace / "tools/yt-dlp/unused/yt-dlp",
        deno=workspace / "tools/deno/unused/deno",
    )
    paths.jobs.mkdir(parents=True)
    paths.models_ollama.mkdir(parents=True)
    return paths


class PhaseSixteenGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = TEST_APP

    def test_document_summary_options_and_local_worker_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = workspace_at(root)
            job = workspace.jobs / "JOB-PHASE16"
            work = job / "work"
            work.mkdir(parents=True)
            canonical = write_canonical(
                work / "belge-kaynagi.jsonl",
                ["Bu belge arayüz özet kuyruğunu doğrular."],
            )
            (work / "belge-kaynagi.txt").write_text("source")
            (work / "belge-kaynagi.kalite.json").write_text("{}")
            with (
                patch.dict(os.environ, {"KSI_STATE_DIRECTORY": str(root / "state")}),
                patch("ksi_local.gui.resolve_workspace", return_value=workspace),
            ):
                window = MainWindow()
                window.workspace = workspace
                record = window.store.create_job(
                    job_id="JOB-PHASE16",
                    job_kind=JobKind.DOCUMENT,
                    source=str(canonical),
                    source_language="auto",
                    want_subtitle=False,
                    want_summary=True,
                    want_dub=False,
                    job_directory=job,
                    summary_profile="detailed",
                    document_summary_source="source",
                    document_summary_pdf=True,
                )
                window.store.transition_job(record.id, JobStatus.RUNNING)
                window.store.transition_job(record.id, JobStatus.EXTRACTED)
                with patch.object(window, "_run_next"):
                    window._launch_document_remaining(window.store.get_job(record.id))
                self.assertEqual([item.stage for item in window.pending], ["document_summarize"])
                command = window.pending[0].argv
                self.assertIn("summarize-document", command)
                self.assertIn("detailed", command)
                self.assertIn("source", command)
                self.assertIn("--pdf", command)
                self.assertNotIn("api.openai.com", " ".join(command))
                window.close()

    def test_translation_then_summary_are_serialized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = workspace_at(root)
            job = workspace.jobs / "JOB-BOTH"
            work = job / "work"
            work.mkdir(parents=True)
            canonical = write_canonical(
                work / "belge-kaynagi.jsonl", ["An English document source block."]
            )
            (work / "belge-kaynagi.txt").write_text("source")
            (work / "belge-kaynagi.kalite.json").write_text("{}")
            with (
                patch.dict(os.environ, {"KSI_STATE_DIRECTORY": str(root / "state")}),
                patch("ksi_local.gui.resolve_workspace", return_value=workspace),
            ):
                window = MainWindow()
                window.workspace = workspace
                record = window.store.create_job(
                    job_id="JOB-BOTH",
                    job_kind=JobKind.DOCUMENT,
                    source=str(canonical),
                    source_language="auto",
                    want_subtitle=True,
                    want_summary=True,
                    want_dub=False,
                    job_directory=job,
                    document_summary_source="auto",
                )
                window.store.transition_job(record.id, JobStatus.RUNNING)
                window.store.transition_job(record.id, JobStatus.EXTRACTED)
                with patch.object(window, "_run_next"):
                    window._launch_document_remaining(window.store.get_job(record.id))
                self.assertEqual(
                    [item.stage for item in window.pending],
                    ["document_translate", "document_summarize"],
                )
                summary_command = window.pending[1].argv
                self.assertIn("--translation", summary_command)
                self.assertIn("--translation-quality", summary_command)
                window.close()

    def test_summary_review_reports_document_evidence_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical = write_canonical(
                root / "source.jsonl", ["Bu belge inceleme penceresini doğrular."]
            )
            result = summarize_document(
                canonical, root / "out", client=SummaryClient()
            )
            dialog = SummaryReviewDialog(
                result.markdown_path, quality_path=result.quality_path
            )
            labels = " ".join(item.text() for item in dialog.findChildren(QLabel))
            self.assertIn("birebir kanıt %100", labels)
            self.assertIn("konum %100", labels)
            dialog.close()


if __name__ == "__main__":
    unittest.main()
