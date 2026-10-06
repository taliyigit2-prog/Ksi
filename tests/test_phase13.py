from __future__ import annotations

import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialogButtonBox
from pypdf import PdfWriter

from ksi_local.document_security import (
    DocumentFormat,
    DocumentImportCancelled,
    DocumentInspection,
    import_document_source,
    inspect_document,
    inspect_document_isolated,
)
from ksi_local.gui import MainWindow, PreflightDialog, PreflightSnapshot
from ksi_local.job_store import JobKind, JobStatus
from ksi_local.preflight import FormatChoice, PreflightResult, inspect_source
from ksi_local.settings import WorkspacePaths


CONTENT_TYPES = (
    '<?xml version="1.0"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="xml" ContentType="application/xml"/>'
    "</Types>"
)
ROOT_RELS = (
    '<?xml version="1.0"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    "</Relationships>"
)
DOCUMENT_XML = (
    '<?xml version="1.0"?>'
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    "<w:body><w:p><w:r><w:t>This is a sufficiently long English document for reliable "
    "offline language detection.</w:t></w:r></w:p></w:body></w:document>"
)


def make_docx(
    path: Path,
    *,
    content_types: str = CONTENT_TYPES,
    root_relationships: str = ROOT_RELS,
    extras: dict[str, bytes | str] | None = None,
) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr("[Content_Types].xml", content_types)
        package.writestr("_rels/.rels", root_relationships)
        package.writestr("word/document.xml", DOCUMENT_XML)
        for name, content in (extras or {}).items():
            package.writestr(name, content)
    return path


def make_pdf(path: Path, *, encrypted: bool = False, javascript: bool = False) -> Path:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    if javascript:
        writer.add_js("app.alert('not allowed')")
    if encrypted:
        writer.encrypt("secret")
    with path.open("wb") as handle:
        writer.write(handle)
    return path


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
    return paths


class DocumentSecurityTests(unittest.TestCase):
    def test_txt_and_markdown_are_utf8_bounded_and_detected_offline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            text = root / "notes.txt"
            text.write_text(
                "This is a long English note that can be detected without any internet call.\n",
                encoding="utf-8",
            )
            markdown = root / "notes.md"
            markdown.write_text(
                "# Local document\n\nThis English paragraph is long enough for detection.\n",
                encoding="utf-8",
            )
            text_result = inspect_document_isolated(text)
            markdown_result = inspect_document(markdown)
            self.assertTrue(text_result.accepted)
            self.assertEqual(text_result.detected_language, "en")
            self.assertEqual(markdown_result.format, DocumentFormat.MARKDOWN)
            self.assertGreaterEqual(markdown_result.block_count or 0, 2)

    def test_extension_signature_mismatch_and_binary_text_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_pdf = root / "fake.pdf"
            fake_pdf.write_text("not a pdf", encoding="utf-8")
            binary = root / "binary.txt"
            binary.write_bytes(b"hello\x00world")
            with self.assertRaisesRegex(ValueError, "PDF imzası"):
                inspect_document(fake_pdf)
            with self.assertRaisesRegex(ValueError, "NUL"):
                inspect_document(binary)

    def test_symlink_and_unsafe_filename_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "safe.txt"
            source.write_text("A sufficiently long English document for a test.", encoding="utf-8")
            link = root / "link.txt"
            link.symlink_to(source)
            with self.assertRaisesRegex(ValueError, "Sembolik"):
                inspect_document(link)
            unsafe = root / "bad:name.txt"
            unsafe.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "güvenli olmayan"):
                inspect_document(unsafe)

    def test_valid_docx_reports_blocks_language_and_package_bounds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = make_docx(Path(directory) / "valid.docx")
            result = inspect_document_isolated(path)
            self.assertTrue(result.accepted)
            self.assertEqual(result.format, DocumentFormat.DOCX)
            self.assertEqual(result.block_count, 1)
            self.assertEqual(result.detected_language, "en")
            self.assertEqual(result.package_entries, 3)

    def test_turkish_document_preview_uses_document_language_detector(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "turkish.txt"
            path.write_text(
                "Bu Türkçe belge yalnızca yerel bilgisayarda işlenir ve hiçbir kaynak dosya buluta gönderilmez.",
                encoding="utf-8",
            )
            result = inspect_document(path)
            self.assertEqual(result.detected_language, "tr")

    def test_docx_macro_external_relationship_embedding_and_traversal_are_blocked(self) -> None:
        external_rels = (
            '<?xml version="1.0"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="https://example.com" TargetMode="External"/>'
            "</Relationships>"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            risky = make_docx(
                root / "risky.docx",
                content_types=CONTENT_TYPES.replace("</Types>", "macroEnabled</Types>"),
                root_relationships=external_rels,
                extras={
                    "word/vbaProject.bin": b"macro",
                    "word/embeddings/object.bin": b"embedded",
                },
            )
            result = inspect_document(risky)
            self.assertFalse(result.accepted)
            self.assertTrue(any("makro" in item for item in result.blocking_reasons))
            self.assertTrue(any("harici" in item for item in result.blocking_reasons))
            self.assertTrue(any("gömülü" in item for item in result.blocking_reasons))

            traversal = root / "traversal.docx"
            make_docx(traversal, extras={"../escape.txt": b"no"})
            with self.assertRaisesRegex(ValueError, "yol geçişi"):
                inspect_document(traversal)

    def test_docx_zip_bomb_ratio_is_rejected_before_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = make_docx(
                Path(directory) / "bomb.docx",
                extras={"word/media/repeated.txt": b"A" * 2_000_000},
            )
            with self.assertRaisesRegex(ValueError, "sıkıştırma oranı"):
                inspect_document(path)

    def test_pdf_page_tree_encryption_actions_and_truncation_are_classified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            clean = inspect_document(make_pdf(root / "clean.pdf"))
            encrypted = inspect_document(make_pdf(root / "encrypted.pdf", encrypted=True))
            scripted = inspect_document(make_pdf(root / "scripted.pdf", javascript=True))
            self.assertTrue(clean.accepted)
            self.assertEqual(clean.page_count, 1)
            self.assertFalse(encrypted.accepted)
            self.assertTrue(any("Şifreli" in item for item in encrypted.blocking_reasons))
            self.assertFalse(scripted.accepted)
            self.assertTrue(any("JavaScript" in item for item in scripted.blocking_reasons))
            truncated = root / "truncated.pdf"
            truncated.write_bytes((root / "clean.pdf").read_bytes()[:-20])
            with self.assertRaisesRegex(ValueError, "tamamlanmamış"):
                inspect_document(truncated)

    def test_pdf_marker_text_inside_metadata_is_not_treated_as_an_action(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "safe-marker-text.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=612, height=792)
            writer.add_metadata({"/Subject": "Harmless text containing /JS and /AA."})
            with path.open("wb") as handle:
                writer.write(handle)
            result = inspect_document(path)
            self.assertTrue(result.accepted)
            self.assertFalse(result.blocking_reasons)


class AtomicDocumentImportTests(unittest.TestCase):
    def test_import_is_hash_verified_hidden_until_publish_and_collision_safe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.txt"
            source.write_text(
                "This source is copied atomically and checked with a secure digest.",
                encoding="utf-8",
            )
            inspection = inspect_document(source)
            destination = root / "job/source"
            imported = import_document_source(
                source,
                destination,
                expected_size=inspection.size_bytes,
                expected_sha256=inspection.sha256,
            )
            self.assertEqual(imported.path.name, "original.txt")
            self.assertEqual(imported.path.read_bytes(), source.read_bytes())
            self.assertFalse(list(destination.glob("*.part")))
            with self.assertRaises(FileExistsError):
                import_document_source(
                    source,
                    destination,
                    expected_size=inspection.size_bytes,
                    expected_sha256=inspection.sha256,
                )

    def test_publish_failure_leaves_no_visible_or_hidden_partial_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.md"
            source.write_text(
                "# Test\n\nThis document remains safe when publishing is interrupted.",
                encoding="utf-8",
            )
            inspection = inspect_document(source)
            destination = root / "job/source"
            with patch(
                "ksi_local.document_security._rename_exclusive",
                side_effect=OSError("SSD removed"),
            ), self.assertRaises(OSError):
                import_document_source(
                    source,
                    destination,
                    expected_size=inspection.size_bytes,
                    expected_sha256=inspection.sha256,
                )
            self.assertFalse((destination / "original.md").exists())
            self.assertFalse(list(destination.glob(".document-import-*.part")))

    def test_cancel_before_publish_leaves_no_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.txt"
            source.write_text(
                "This document import is cancelled before it becomes visible.",
                encoding="utf-8",
            )
            inspection = inspect_document(source)
            destination = root / "job/source"
            with self.assertRaises(DocumentImportCancelled):
                import_document_source(
                    source,
                    destination,
                    expected_size=inspection.size_bytes,
                    expected_sha256=inspection.sha256,
                    cancel_check=lambda: True,
                )
            self.assertFalse((destination / "original.txt").exists())
            self.assertFalse(list(destination.glob(".document-import-*.part")))


class DocumentPreflightTests(unittest.TestCase):
    def test_auto_mode_builds_document_preflight_without_loading_ai(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = workspace_at(root)
            source = root / "document.txt"
            source.write_text(
                "This is a sufficiently long English document for local inspection.",
                encoding="utf-8",
            )
            with patch(
                "ksi_local.preflight.shutil.disk_usage",
                return_value=SimpleNamespace(free=80 * 1024**3),
            ):
                result = inspect_source(
                    str(source),
                    workspace=workspace,
                    download_only=False,
                    want_subtitle=True,
                    want_summary=True,
                    job_kind="auto",
                )
            self.assertEqual(result.source_kind, "document")
            self.assertIsNotNone(result.document)
            self.assertIn("Türkçe belge çevirisi", result.requested_outputs)
            self.assertTrue(result.fixed_budget and result.fixed_budget.fits)


class PhaseThirteenGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_document_selection_changes_controls_and_preview(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = workspace_at(root)
            with (
                patch.dict(os.environ, {"KSI_STATE_DIRECTORY": str(root / "state")}),
                patch("ksi_local.gui.resolve_workspace", return_value=workspace),
            ):
                window = MainWindow()
                window.source.setText(str(root / "example.pdf"))
                self.assertEqual(window.want_subtitle.text(), "Belgeyi Türkçeye çevir")
                self.assertFalse(window.download_only.isEnabled())
                self.assertFalse(window.want_dub.isEnabled())
                window.close()

        inspection = DocumentInspection(
            format=DocumentFormat.PDF,
            format_label="PDF",
            filename="example.pdf",
            size_bytes=100,
            sha256="a" * 64,
            page_count=2,
            block_count=None,
            detected_language="en",
            language_confidence=0.99,
            ocr_likely_pages=1,
            text_preview_available=False,
            accepted=True,
            warnings=("Bir sayfada OCR gerekebilir.",),
            blocking_reasons=(),
        )
        result = PreflightResult(
            source_kind="document",
            platform="Yerel belge",
            title="example.pdf",
            uploader=None,
            duration_seconds=0,
            live_status=None,
            entry_count=1,
            manual_subtitles=(),
            automatic_subtitles=(),
            detected_language=None,
            requires_authentication=False,
            available_heights=(),
            default_height=None,
            format_choices=(),
            fixed_budget=FormatChoice(0, 100, 200, 300, True),
            requested_outputs=("Türkçe özet",),
            free_bytes=1_000,
            retry_count=0,
            warnings=(),
            tool_versions=(),
            document=inspection,
        )
        dialog = PreflightDialog(result)
        self.assertEqual(dialog.windowTitle(), "Belge güvenlik ön incelemesi")
        self.assertEqual(
            dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).text(),
            "Onayla ve SSD'ye Al",
        )
        self.assertTrue(dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled())
        dialog.close()

    def test_completed_import_is_registered_as_document_without_starting_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = workspace_at(root)
            source = root / "document.txt"
            source.write_text("Imported source", encoding="utf-8")
            job_directory = workspace.jobs / "JOB-DOCUMENT"
            imported_path = job_directory / "source/original.txt"
            imported_path.parent.mkdir(parents=True)
            imported_path.write_bytes(source.read_bytes())
            imported = SimpleNamespace(
                path=imported_path,
                size_bytes=imported_path.stat().st_size,
                sha256="b" * 64,
                to_dict=lambda: {
                    "path": str(imported_path),
                    "size_bytes": imported_path.stat().st_size,
                    "sha256": "b" * 64,
                },
            )
            inspection = DocumentInspection(
                format=DocumentFormat.TEXT,
                format_label="Düz metin (TXT)",
                filename="document.txt",
                size_bytes=imported_path.stat().st_size,
                sha256="b" * 64,
                page_count=None,
                block_count=1,
                detected_language="en",
                language_confidence=0.9,
                ocr_likely_pages=0,
                text_preview_available=True,
                accepted=True,
                warnings=(),
                blocking_reasons=(),
            )
            result = PreflightResult(
                source_kind="document",
                platform="Yerel belge",
                title="document.txt",
                uploader=None,
                duration_seconds=0,
                live_status=None,
                entry_count=1,
                manual_subtitles=(),
                automatic_subtitles=(),
                detected_language="en",
                requires_authentication=False,
                available_heights=(),
                default_height=None,
                format_choices=(),
                fixed_budget=FormatChoice(0, 10, 20, 30, True),
                requested_outputs=("Türkçe özet",),
                free_bytes=1_000,
                retry_count=0,
                warnings=(),
                tool_versions=(),
                document=inspection,
            )
            snapshot = PreflightSnapshot(
                source=str(source),
                job_kind="document",
                source_language="auto",
                download_only=False,
                want_subtitle=False,
                want_summary=True,
                want_dub=False,
                browser=None,
                browser_profile=None,
                udemy_access_confirmed=False,
            )
            with (
                patch.dict(os.environ, {"KSI_STATE_DIRECTORY": str(root / "state")}),
                patch("ksi_local.gui.resolve_workspace", return_value=workspace),
            ):
                window = MainWindow()
                window.workspace = workspace
                with patch.object(window, "_launch_document_extraction") as launch:
                    window._document_import_finished(
                        imported, None, "JOB-DOCUMENT", snapshot, result
                    )
                record = window.store.get_job("JOB-DOCUMENT")
                self.assertEqual(record.job_kind, JobKind.DOCUMENT)
                self.assertEqual(record.status, JobStatus.IMPORTED)
                self.assertIsNone(window.process)
                launch.assert_called_once()
                window.close()


if __name__ == "__main__":
    unittest.main()
