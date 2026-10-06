from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ksi_local.document_translation import (
    document_needs_model,
    pilot_language_matrix,
    translate_document,
    validate_document_glossary,
)
from ksi_local.gui import DocumentTranslationReviewDialog, MainWindow
from ksi_local.job_store import JobKind, JobStatus
from ksi_local.language_detection import detect_document_language
from ksi_local.settings import WorkspacePaths


def write_canonical(path: Path, items: list[tuple[str, str, str]]) -> Path:
    records = []
    for sequence, (block_type, text, language) in enumerate(items, start=1):
        records.append(
            {
                "schema_version": 1,
                "id": f"B{sequence:06d}",
                "sequence": sequence,
                "source_sha256": "a" * 64,
                "source_format": "md",
                "block_type": block_type,
                "text": text,
                "location": {"line_start": sequence, "line_end": sequence},
                "extraction_method": "test",
                "detected_language": language,
                "language_confidence": 1.0,
                "ocr_confidence": None,
                "style": {"heading_level": 1} if block_type == "heading" else {},
                "flags": [],
            }
        )
    path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n",
        encoding="utf-8",
    )
    return path


class FakeClient:
    def __init__(self, *, fail_after: int | None = None, bad_contract: bool = False) -> None:
        self.calls: list[dict[str, object]] = []
        self.fail_after = fail_after
        self.bad_contract = bad_contract

    def generate(self, **kwargs: object) -> str:
        self.calls.append(kwargs)
        if self.fail_after is not None and len(self.calls) > self.fail_after:
            raise RuntimeError("planned interruption")
        prompt = str(kwargs["prompt"])
        items = json.loads(prompt.rsplit("INPUT:\n", 1)[1])
        records = []
        for item in items:
            text = str(item["text"])
            text = text.replace("artificial intelligence", "yapay zekâ")
            records.append(
                {
                    "id": "BAD" if self.bad_contract else item["id"],
                    "text": "Türkçe çeviri: " + text,
                }
            )
        return json.dumps({"translations": records}, ensure_ascii=False)


class RecoveringContractClient(FakeClient):
    def __init__(self, mode: str) -> None:
        super().__init__()
        self.mode = mode

    def generate(self, **kwargs: object) -> str:
        raw = super().generate(**kwargs)
        payload = json.loads(raw)
        records = payload["translations"]
        if len(records) > 1:
            if self.mode == "reordered":
                records.reverse()
            elif self.mode == "duplicate":
                records[1]["id"] = records[0]["id"]
            elif self.mode == "missing":
                records.pop()
        return json.dumps(payload, ensure_ascii=False)


class SourceMarkerClient(FakeClient):
    def generate(self, **kwargs: object) -> str:
        payload = json.loads(super().generate(**kwargs))
        payload["translations"][0]["text"] += " B999999"
        return json.dumps(payload, ensure_ascii=False)


class TransientProtectedMarkerClient(FakeClient):
    def generate(self, **kwargs: object) -> str:
        payload = json.loads(super().generate(**kwargs))
        if len(self.calls) == 1:
            payload["translations"][0]["text"] = payload["translations"][0][
                "text"
            ].replace("VTRTOKEN", "VTRCHANGED", 1)
        return json.dumps(payload, ensure_ascii=False)


class TransientNumericLeakClient(FakeClient):
    def generate(self, **kwargs: object) -> str:
        payload = json.loads(super().generate(**kwargs))
        if len(self.calls) == 1:
            payload["translations"][0]["text"] += " 000001"
        return json.dumps(payload, ensure_ascii=False)


class LocalizedNumberClient(FakeClient):
    def generate(self, **kwargs: object) -> str:
        payload = json.loads(super().generate(**kwargs))
        payload["translations"][0]["text"] = payload["translations"][0]["text"].replace(
            "48,750", "48.750"
        ).replace("4.8", "4,8").replace("3.7%", "%3,7")
        return json.dumps(payload, ensure_ascii=False)


class NaturalNormalizationClient(FakeClient):
    def generate(self, **kwargs: object) -> str:
        self.calls.append(kwargs)
        item = json.loads(str(kwargs["prompt"]).rsplit("INPUT:\n", 1)[1])[0]
        return json.dumps(
            {
                "translations": [{
                    "id": item["id"],
                    "text": (
                        "Bütçe 48.750 EUR ve 12.400 ABD doları, hata %3,7 ve süre 900'i aşmamalıdır. "
                        "İnceleme 9 de julio de 2026 tarihinde."
                    ),
                }]
            },
            ensure_ascii=False,
        )


class UntranslatedHeadingClient(FakeClient):
    def generate(self, **kwargs: object) -> str:
        self.calls.append(kwargs)
        item = json.loads(str(kwargs["prompt"]).rsplit("INPUT:\n", 1)[1])[0]
        return json.dumps(
            {"translations": [{"id": item["id"], "text": item["text"]}]},
            ensure_ascii=False,
        )


class RedundantMarkdownDelimiterClient(FakeClient):
    def generate(self, **kwargs: object) -> str:
        self.calls.append(kwargs)
        item = json.loads(str(kwargs["prompt"]).rsplit("INPUT:\n", 1)[1])[0]
        marker = re.search(r"VTRTOKEN\d{9}X", str(item["text"])).group(0)
        return json.dumps(
            {"translations": [{"id": item["id"], "text": f"[Politika]({marker}).) görüntüleyin."}]},
            ensure_ascii=False,
        )


class TransientNegationDriftClient(FakeClient):
    def generate(self, **kwargs: object) -> str:
        self.calls.append(kwargs)
        prompt = str(kwargs["prompt"])
        item = json.loads(prompt.rsplit("INPUT:\n", 1)[1])[0]
        if len(self.calls) == 1:
            text = "Basınç limiti 4.8 bar'dır; 8.4 bar'ın altında değildir."
        else:
            text = "Basınç limiti 4.8 bar'dır; 8.4 bar değildir."
        return json.dumps(
            {"translations": [{"id": item["id"], "text": text}]},
            ensure_ascii=False,
        )


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


class DocumentTranslationCoreTests(unittest.TestCase):
    def test_dates_currency_and_multiword_names_are_restored_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical = write_canonical(
                root / "belge-kaynagi.jsonl",
                [(
                    "paragraph",
                    "Reviewer Deniz Kaya approved EUR 48,750 on 20 September 2026.",
                    "en",
                )],
            )
            result = translate_document(canonical, root / "outputs", client=FakeClient())
            translated = json.loads(result.jsonl_path.read_text().splitlines()[0])[
                "translated_text"
            ]
            self.assertIn("Deniz Kaya", translated)
            self.assertIn("EUR 48,750", translated)
            self.assertIn("20 Eylül 2026 tarihinde", translated)
            self.assertNotIn("VTRTOKEN", translated)

    def test_all_seven_stable_languages_are_translated_in_direct_packages(self) -> None:
        samples = [
            ("en", "This English paragraph contains enough natural words for reliable language detection."),
            ("ru", "Этот русский абзац содержит достаточно естественных слов для надежного определения языка."),
            ("es", "Este párrafo español contiene suficientes palabras naturales para detectar bien el idioma."),
            ("de", "Dieser deutsche Absatz enthält genügend natürliche Wörter für eine zuverlässige Spracherkennung."),
            ("zh", "这个中文段落包含足够多的自然词语，可以可靠地检测文档语言。"),
            ("fr", "Ce paragraphe français contient suffisamment de mots naturels pour détecter correctement la langue."),
            ("it", "Questo paragrafo italiano contiene abbastanza parole naturali per rilevare correttamente la lingua."),
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical = write_canonical(
                root / "belge-kaynagi.jsonl",
                [("paragraph", text, language) for language, text in samples],
            )
            result = translate_document(
                canonical,
                root / "outputs",
                client=FakeClient(),
                batch_size=1,
            )
            records = [json.loads(line) for line in result.jsonl_path.read_text().splitlines()]
            self.assertEqual([record["source_language"] for record in records], [code for code, _ in samples])
            self.assertEqual(result.translated_block_count, 7)
            self.assertEqual(len(result.language_counts), 7)

    def test_mixed_languages_structure_and_turkish_code_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical = write_canonical(
                root / "belge-kaynagi.jsonl",
                [
                    ("heading", "This English heading contains enough words for detection.", "en"),
                    ("paragraph", "Этот русский абзац содержит достаточно слов для определения языка.", "ru"),
                    ("paragraph", "Bu Türkçe paragraf olduğu gibi korunmalı ve modele gönderilmemelidir.", "tr"),
                    ("code_block", "print('never translate 42')", "en"),
                ],
            )
            client = FakeClient()
            result = translate_document(
                canonical,
                root / "outputs",
                client=client,
                batch_size=1,
            )
            records = [json.loads(line) for line in result.jsonl_path.read_text().splitlines()]
            self.assertEqual([record["id"] for record in records], [f"B{x:06d}" for x in range(1, 5)])
            self.assertEqual(records[0]["block_type"], "heading")
            self.assertEqual(records[1]["source_language"], "ru")
            self.assertEqual(records[2]["status"], "preserved_turkish")
            self.assertEqual(records[2]["source_text"], records[2]["translated_text"])
            self.assertEqual(records[3]["status"], "preserved_code")
            self.assertEqual(len(client.calls), 2)
            self.assertEqual(result.translated_block_count, 2)

    def test_protected_values_glossary_hashes_and_checkpoint_are_verified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical = write_canonical(
                root / "belge-kaynagi.jsonl",
                [("paragraph", "OpenAI artificial intelligence reached 42% at https://example.com; contact team@example.com and keep `x += 1` with $E=mc^2$.", "en")],
            )
            glossary = root / "custom.json"
            glossary.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "target_language": "tr",
                        "preserve": ["OpenAI"],
                        "terms": {"en": {"artificial intelligence": "yapay zekâ"}},
                    }
                ),
                encoding="utf-8",
            )
            details = validate_document_glossary(glossary)
            self.assertEqual(details["language_count"], 1)
            client = FakeClient()
            result = translate_document(
                canonical,
                root / "outputs",
                client=client,
                custom_glossary=glossary,
            )
            self.assertNotIn(
                "Preserve these names exactly: OpenAI", str(client.calls[0]["prompt"])
            )
            record = json.loads(result.jsonl_path.read_text().splitlines()[0])
            for value in (
                "OpenAI",
                "42%",
                "https://example.com",
                "team@example.com",
                "`x += 1`",
                "$E=mc^2$",
            ):
                self.assertIn(value, record["translated_text"])
            self.assertEqual(record["source_block_sha256"], __import__("hashlib").sha256(record["source_text"].encode()).hexdigest())
            self.assertEqual(record["translation_sha256"], __import__("hashlib").sha256(record["translated_text"].encode()).hexdigest())
            checkpoint = json.loads(result.checkpoint_path.read_text())
            self.assertTrue(checkpoint["complete"])
            self.assertEqual(checkpoint["completed_blocks"], 1)
            self.assertTrue(result.quality["passed"])

    def test_transient_protected_marker_corruption_is_retried(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical = write_canonical(
                root / "belge-kaynagi.jsonl",
                [("paragraph", "OpenAI published 42 examples at https://example.com.", "en")],
            )
            client = TransientProtectedMarkerClient()
            result = translate_document(canonical, root / "outputs", client=client)
            record = json.loads(result.jsonl_path.read_text().splitlines()[0])
            self.assertEqual(len(client.calls), 2)
            self.assertIn("OpenAI", record["translated_text"])
            self.assertIn("42", record["translated_text"])
            self.assertIn("https://example.com", record["translated_text"])

    def test_transient_numeric_marker_leak_is_retried(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical = write_canonical(
                root / "belge-kaynagi.jsonl",
                [("paragraph", "The safe limit is 42 units.", "en")],
            )
            client = TransientNumericLeakClient()
            result = translate_document(canonical, root / "outputs", client=client)
            record = json.loads(result.jsonl_path.read_text().splitlines()[0])
            self.assertEqual(len(client.calls), 2)
            self.assertNotIn("000001", record["translated_text"])
            self.assertIn("42", record["translated_text"])

    def test_localized_number_punctuation_is_restored_to_source_spelling(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical = write_canonical(
                root / "belge-kaynagi.jsonl",
                [("paragraph", "The budget is EUR 48,750, pressure is 4.8 bar, and drift is 3.7%.", "en")],
            )
            result = translate_document(
                canonical,
                root / "outputs",
                client=LocalizedNumberClient(),
            )
            record = json.loads(result.jsonl_path.read_text().splitlines()[0])
            self.assertIn("48,750", record["translated_text"])
            self.assertIn("4.8", record["translated_text"])
            self.assertIn("3.7%", record["translated_text"])
            self.assertNotIn("48.750", record["translated_text"])
            self.assertNotIn("4,8", record["translated_text"])

    def test_percent_date_unit_and_numeric_suffix_are_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical = write_canonical(
                root / "belge-kaynagi.jsonl",
                [(
                    "paragraph",
                    "Budget EUR 48,750 and USD 12,400, error 3.7%, duration 900 s. Review 9 July 2026.",
                    "en",
                )],
            )
            result = translate_document(
                canonical,
                root / "outputs",
                client=NaturalNormalizationClient(),
            )
            translated = json.loads(result.jsonl_path.read_text().splitlines()[0])[
                "translated_text"
            ]
            self.assertIn("EUR 48,750", translated)
            self.assertIn("USD 12,400", translated)
            self.assertIn("3.7%", translated)
            self.assertNotIn("%3.7%", translated)
            self.assertIn("900 s'i", translated)
            self.assertIn("9 Temmuz 2026 tarihinde", translated)

    def test_ocr_surface_errors_are_repaired_without_changing_facts(self) -> None:
        from ksi_local.document_translation import _normalize_translation

        self.assertEqual(
            _normalize_translation("Site: Harbor Station 7", "Web sitesi: Harbor Station 7"),
            "Tesis: Harbor Station 7",
        )
        self.assertEqual(
            _normalize_translation(
                "The maintenance window begins at 06:30 and ends at 09:15.",
                "Bakım süresi 06:30'te başlar ve 09:15.'te sona erer.",
            ),
            "Bakım süresi 06:30'da başlar ve 09:15'te sona erer.",
        )
        self.assertEqual(
            _normalize_translation(
                "Exactly 12 technicians may enter the restricted area.",
                "Tam olarak 12 teknisyenleri kısıtlı alana girebilir.",
            ),
            "Kısıtlı alana tam olarak 12 teknisyen girebilir.",
        )
        self.assertEqual(
            _normalize_translation(
                "Report any fault to safety@example.org before 10:00.",
                "Arızayı safety@example.org'e 10:00.'ten önce bildirin.",
            ),
            "Arızayı safety@example.org adresine 10:00'dan önce bildirin.",
        )
        self.assertEqual(
            _normalize_translation("Date: 18 April 2026", "Tarih: 18 Nisan 2026 tarihinde"),
            "Tarih: 18 Nisan 2026",
        )
        self.assertEqual(
            _normalize_translation("It fell to 1.9%.", "1,9%.'e düştü."),
            "1.9%'e düştü.",
        )
        self.assertEqual(
            _normalize_translation(
                "There were 17 local tests on 5 May 2026.",
                "5 Mayıs 2026. tarihinde 17 yerel testleri vardı.",
            ),
            "5 Mayıs 2026 tarihinde 17 yerel testi vardı.",
        )
        self.assertEqual(
            _normalize_translation(
                "Section 4 contains 1035 records.",
                "Bölüm 4'te 1035 kayıtları bulunmaktadır.",
            ),
            "Bölüm 4'te 1035 kayıt bulunmaktadır.",
        )
        self.assertEqual(
            _normalize_translation(
                "Reviewer Deniz Kaya checked batch AT-17 locally.",
                "Değerlendirici Deniz Kaya, toplama AT-17'i yerel olarak kontrol etti.",
            ),
            "Değerlendirici Deniz Kaya, AT-17 grubunu yerel olarak kontrol etti.",
        )
        self.assertEqual(
            _normalize_translation(
                "Controlled scan for OCR review • Page 1 of 1",
                "OCR incelemesi için kontrollü tarama • 1 sayfasının 1'deki içeriği",
            ),
            "OCR incelemesi için kontrollü tarama • Sayfa 1 / 1",
        )
        self.assertEqual(
            _normalize_translation(
                "This memo does not permit remote processing and does not cancel the",
                "Bu not, uzaktan işlem yapılmasını sağlamaz ve mevcut şartları yürürlükte tutar.",
            ),
            "Bu not uzaktan işlemeye izin vermez ve aşağıdaki uygulamayı iptal etmez:",
        )
        self.assertEqual(
            _normalize_translation("emergency procedure.", "acil durum prosedürü."),
            "Acil durum prosedürü.",
        )

    def test_untranslated_generic_heading_uses_local_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical = write_canonical(
                root / "belge-kaynagi.jsonl",
                [("heading", "Local Processing Guide", "en")],
            )
            client = UntranslatedHeadingClient()
            result = translate_document(canonical, root / "outputs", client=client)
            translated = json.loads(result.jsonl_path.read_text().splitlines()[0])[
                "translated_text"
            ]
            self.assertEqual(translated, "Yerel İşleme Rehberi")
            self.assertEqual(len(client.calls), 1)

    def test_one_redundant_markdown_link_delimiter_is_repaired(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical = write_canonical(
                root / "belge-kaynagi.jsonl",
                [("paragraph", "See [the policy](https://example.org/policy).", "en")],
            )
            result = translate_document(
                canonical,
                root / "outputs",
                client=RedundantMarkdownDelimiterClient(),
            )
            translated = json.loads(result.jsonl_path.read_text().splitlines()[0])[
                "translated_text"
            ]
            self.assertEqual(translated, "[Politika](https://example.org/policy). görüntüleyin.")

    def test_simple_negation_cannot_gain_a_comparison_meaning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical = write_canonical(
                root / "belge-kaynagi.jsonl",
                [("paragraph", "The pressure limit is 4.8 bar; it is not 8.4 bar.", "en")],
            )
            client = TransientNegationDriftClient()
            result = translate_document(canonical, root / "outputs", client=client)
            record = json.loads(result.jsonl_path.read_text().splitlines()[0])
            self.assertEqual(len(client.calls), 2)
            self.assertEqual(
                record["translated_text"],
                "Basınç limiti 4.8 bar'dır; 8.4 bar değildir.",
            )
            self.assertIn("previous answer failed", str(client.calls[1]["prompt"]))

    def test_checkpoint_resumes_and_malformed_model_contract_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical = write_canonical(
                root / "belge-kaynagi.jsonl",
                [
                    ("paragraph", f"English document paragraph number {index} contains enough text for language detection.", "en")
                    for index in range(1, 4)
                ],
            )
            with self.assertRaisesRegex(RuntimeError, "planned interruption"):
                translate_document(
                    canonical,
                    root / "outputs",
                    client=FakeClient(fail_after=1),
                    batch_size=1,
                )
            checkpoint = json.loads((root / "belge-ceviri.checkpoint.json").read_text())
            self.assertEqual(checkpoint["completed_blocks"], 1)
            resumed = FakeClient()
            result = translate_document(
                canonical,
                root / "outputs",
                client=resumed,
                batch_size=1,
            )
            self.assertEqual(len(resumed.calls), 2)
            self.assertEqual(result.block_count, 3)

            bad_root = root / "bad"
            bad_root.mkdir()
            bad = write_canonical(
                bad_root / "belge-kaynagi.jsonl",
                [("paragraph", "This English paragraph is long enough for safe detection.", "en")],
            )
            with self.assertRaisesRegex(RuntimeError, "blok sözleşmesini"):
                translate_document(bad, bad_root / "outputs", client=FakeClient(bad_contract=True))

    def test_reordered_duplicate_missing_and_source_marker_responses_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            items = [
                ("paragraph", "This is the first sufficiently long English source paragraph.", "en"),
                ("paragraph", "This is the second sufficiently long English source paragraph.", "en"),
            ]
            for mode in ("reordered", "duplicate", "missing"):
                case = root / mode
                case.mkdir()
                canonical = write_canonical(case / "belge-kaynagi.jsonl", items)
                client = RecoveringContractClient(mode)
                result = translate_document(canonical, case / "outputs", client=client)
                self.assertEqual(result.block_count, 2)
                self.assertEqual(len(client.calls), 3)
            marker = root / "marker"
            marker.mkdir()
            canonical = write_canonical(marker / "belge-kaynagi.jsonl", items[:1])
            with self.assertRaisesRegex(RuntimeError, "blok işaretini"):
                translate_document(canonical, marker / "outputs", client=SourceMarkerClient())

    def test_turkish_document_skips_model_and_pilot_languages_stay_gated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical = write_canonical(
                root / "belge-kaynagi.jsonl",
                [("paragraph", "Bu uzun Türkçe paragraf model çalıştırılmadan korunmalıdır.", "tr")],
            )
            self.assertFalse(document_needs_model(canonical))
            result = translate_document(canonical, root / "outputs", client=None)
            self.assertEqual(result.preserved_block_count, 1)
            matrix = pilot_language_matrix()
            self.assertEqual(len(matrix["candidates"]), 10)
            self.assertTrue(all(item["status"] == "gated_not_selectable" for item in matrix["candidates"]))
            self.assertEqual(detect_document_language("Este texto português contém palavras suficientes para identificar corretamente o idioma.").code, "pt")


class PhaseFifteenGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_extracted_document_queues_local_translation_and_review_opens(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = workspace_at(root)
            job = workspace.jobs / "JOB-PHASE15"
            work = job / "work"
            work.mkdir(parents=True)
            canonical = write_canonical(
                work / "belge-kaynagi.jsonl",
                [("paragraph", "This English paragraph has enough words to translate safely.", "en")],
            )
            (work / "belge-kaynagi.txt").write_text("source", encoding="utf-8")
            (work / "belge-kaynagi.kalite.json").write_text("{}", encoding="utf-8")
            with (
                patch.dict(os.environ, {"KSI_STATE_DIRECTORY": str(root / "state")}),
                patch("ksi_local.gui.resolve_workspace", return_value=workspace),
            ):
                window = MainWindow()
                window.workspace = workspace
                record = window.store.create_job(
                    job_id="JOB-PHASE15",
                    job_kind=JobKind.DOCUMENT,
                    source=str(canonical),
                    source_language="auto",
                    want_subtitle=True,
                    want_summary=False,
                    want_dub=False,
                    job_directory=job,
                )
                window.store.transition_job(record.id, JobStatus.RUNNING)
                window.store.transition_job(record.id, JobStatus.EXTRACTED)
                with patch.object(window, "_run_next"):
                    window._launch_document_translation(window.store.get_job(record.id))
                self.assertEqual(window.pending[0].stage, "document_translate")
                command = " ".join(window.pending[0].argv)
                self.assertIn("translate-document", command)
                self.assertIn("--models-directory", command)
                self.assertNotIn("api.openai.com", command)
                window.close()

    def test_review_dialog_shows_source_translation_and_quality(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical = write_canonical(
                root / "belge-kaynagi.jsonl",
                [("heading", "This English heading is sufficiently long for detection.", "en")],
            )
            result = translate_document(canonical, root / "outputs", client=FakeClient())
            dialog = DocumentTranslationReviewDialog(
                result.jsonl_path,
                quality_path=result.quality_path,
            )
            tables = dialog.findChildren(__import__("PySide6.QtWidgets", fromlist=["QTableWidget"]).QTableWidget)
            self.assertEqual(tables[0].rowCount(), 1)
            self.assertIn("Türkçe çeviri", tables[0].item(0, 3).text())
            dialog.close()


if __name__ == "__main__":
    unittest.main()
