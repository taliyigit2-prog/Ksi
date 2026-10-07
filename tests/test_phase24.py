from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (
    QApplication,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QTableWidget,
)

from ksi_local.gui import FirstRunWizard, MainWindow, SubtitleReviewDialog
from ksi_local.i18n import (
    SUPPORTED_UI_LANGUAGES,
    normalize_ui_language,
    ui_text,
    validate_catalogs,
)
from ksi_local.maintenance import format_acceptance_report
from ksi_local.preferences import UserPreferences, load_preferences, save_preferences
from ksi_local.review_package import ReviewPreview, TextChange
from ksi_local.subtitles import Cue, write_srt


class PhaseTwentyFourCatalogTests(unittest.TestCase):
    def test_catalogs_are_complete_and_unknown_locale_falls_back_to_english(self) -> None:
        self.assertEqual(validate_catalogs(), ())
        self.assertEqual(normalize_ui_language("ru_RU"), "ru")
        self.assertEqual(normalize_ui_language("ja-JP"), "en")
        self.assertEqual(ui_text("button.start", "ja"), "Review and Start")

    def test_old_preferences_default_to_turkish_and_language_persists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "preferences.json"
            target.write_text('{"schema_version": 2, "export_kind": "all"}')
            self.assertEqual(load_preferences(target).ui_language, "tr")
            save_preferences(UserPreferences(ui_language="ru"), target)
            payload = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], 7)
            self.assertEqual(load_preferences(target).ui_language, "ru")

    def test_language_order_and_new_catalogs_are_real_translations(self) -> None:
        self.assertEqual(
            SUPPORTED_UI_LANGUAGES,
            ("tr", "ru", "en", "es", "de", "fr", "it", "zh"),
        )
        for language in ("es", "de", "fr", "it", "zh"):
            with self.subTest(language=language):
                self.assertNotEqual(
                    ui_text("header.subtitle", language),
                    ui_text("header.subtitle", "en"),
                )
                self.assertNotEqual(
                    ui_text("preflight.document_heading", language),
                    ui_text("preflight.document_heading", "en"),
                )
                self.assertNotEqual(
                    ui_text("runtime.disconnected", language),
                    ui_text("runtime.disconnected", "en"),
                )


class PhaseTwentyFourGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_live_language_switch_preserves_semantic_combo_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            with (
                patch.dict(os.environ, {"KSI_STATE_DIRECTORY": str(state)}),
                patch.object(MainWindow, "_initialize_workspace", return_value=None),
            ):
                window = MainWindow()
                self.assertEqual(window.preferences.ui_language, "tr")
                source_values = [
                    window.language.itemData(index)
                    for index in range(window.language.count())
                ]
                export_values = [
                    window.export_kind.itemData(index)
                    for index in range(window.export_kind.count())
                ]

                window.ui_language.setCurrentIndex(window.ui_language.findData("ru"))
                self.application.processEvents()

                self.assertEqual(window.new_job_group.title(), "Новая задача")
                self.assertEqual(window.start_button.text(), "Проверить и запустить")
                self.assertEqual(window.history.horizontalHeaderItem(1).text(), "Источник")
                self.assertEqual(
                    window.language.itemText(window.language.findData("en")),
                    "Английский",
                )
                self.assertEqual(
                    [window.language.itemData(index) for index in range(window.language.count())],
                    source_values,
                )
                self.assertEqual(
                    [window.export_kind.itemData(index) for index in range(window.export_kind.count())],
                    export_values,
                )
                self.assertEqual(load_preferences().ui_language, "ru")
                window.resize(820, 860)
                window.show()
                self.application.processEvents()
                self.assertEqual(window.centralWidget().horizontalScrollBar().maximum(), 0)
                window.close()

                reopened = MainWindow()
                self.assertEqual(reopened.preferences.ui_language, "ru")
                self.assertEqual(reopened.header_subtitle.text(), "Переводите видео и документы полностью локально.")
                reopened.ui_language.setCurrentIndex(reopened.ui_language.findData("en"))
                self.application.processEvents()
                self.assertEqual(reopened.output_group.title(), "Requested results")
                self.assertEqual(reopened.export_button.text(), "Copy to Mac")
                reopened.close()

    def test_review_dialog_uses_selected_russian_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.srt"
            translated = root / "translated.srt"
            quality = root / "quality.json"
            write_srt(source, [Cue(1, "00:00:00,000", "00:00:01,000", "Hello")])
            write_srt(translated, [Cue(1, "00:00:00,000", "00:00:01,000", "Merhaba")])
            quality.write_text(
                json.dumps({"error_count": 0, "warning_count": 0, "issues": []}),
                encoding="utf-8",
            )
            dialog = SubtitleReviewDialog(
                translated,
                source_path=source,
                quality_path=quality,
                language="ru",
            )
            self.assertEqual(dialog.windowTitle(), "Проверка турецких субтитров")
            table = dialog.findChild(QTableWidget)
            assert table is not None
            self.assertEqual(table.horizontalHeaderItem(1).text(), "Источник")
            buttons = dialog.findChild(QDialogButtonBox)
            assert buttons is not None
            self.assertEqual(
                buttons.button(QDialogButtonBox.StandardButton.Close).text(),
                "Закрыть",
            )
            dialog.close()

    def test_first_run_wizard_uses_selected_russian_catalog(self) -> None:
        report = SimpleNamespace(
            passed=True,
            workspace="/Volumes/Test/KSI-Workspace",
            workspace_free_bytes=64 * 1024**3,
            tool_integrity_ok=True,
            health=SimpleNamespace(youtube_js_ready=True),
            models=(SimpleNamespace(ready=True),),
            app_bundle=SimpleNamespace(
                exists=True,
                signed=True,
                native_arm64_only=True,
                native_architecture_matches_host=True,
            ),
        )
        dialog = FirstRunWizard(report, language="ru")
        labels = "\n".join(label.text() for label in dialog.findChildren(QLabel))
        self.assertEqual(dialog.windowTitle(), "Первый запуск KSI Local Studio")
        self.assertIn("KSI Local Studio готова к работе", labels)
        self.assertIn("FFmpeg, Ollama, yt-dlp и Deno проверены", labels)
        buttons = dialog.findChild(QDialogButtonBox)
        assert buttons is not None
        self.assertEqual(
            buttons.button(QDialogButtonBox.StandardButton.Ok).text(),
            "Использовать KSI Local Studio",
        )
        dialog.close()

    def test_review_preview_and_system_report_use_russian_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            with (
                patch.dict(os.environ, {"KSI_STATE_DIRECTORY": str(state)}),
                patch.object(MainWindow, "_initialize_workspace", return_value=None),
            ):
                window = MainWindow()
                window.ui_language.setCurrentIndex(window.ui_language.findData("ru"))
                preview = ReviewPreview(
                    package_directory=Path(directory),
                    package_digest="digest",
                    job_id="job",
                    source_language="en",
                    segment_changes=(TextChange("1", "old", "new", ""),),
                    summary_changes=(),
                    subtitle_warning_count=2,
                )
                rendered = window._review_preview_text(preview)
                self.assertIn("Исправлений субтитров: 1", rendered)
                self.assertIn("Предупреждений качества субтитров: 2", rendered)
                window.close()

        report = SimpleNamespace(
            passed=True,
            app_version="2.0",
            architecture="arm64",
            workspace="/Volumes/Test/KSI-Workspace",
            workspace_free_bytes=64 * 1024**3,
            tool_integrity_ok=True,
            app_bundle=SimpleNamespace(signed=True, native_arm64_only=True, native_architecture_matches_host=True),
            models=(
                SimpleNamespace(
                    key="qwen3.5:4b",
                    label="Qwen3.5 4B",
                    ready=True,
                    digest_verified=True,
                    actual_bytes=4 * 1024**3,
                    purpose="Türkçe özet",
                ),
            ),
            cache=SimpleNamespace(candidate_file_count=3, candidate_bytes=1024**2),
        )
        rendered = format_acceptance_report(report, language="ru")
        self.assertIn("Архитектура: arm64", rendered)
        self.assertIn("Резюме на турецком", rendered)
        self.assertNotIn("Yeniden üretilebilir", rendered)

    def test_runtime_storage_message_uses_selected_russian_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            with (
                patch.dict(os.environ, {"KSI_STATE_DIRECTORY": str(state)}),
                patch.object(MainWindow, "_initialize_workspace", return_value=None),
                patch.object(QMessageBox, "information") as information,
            ):
                window = MainWindow()
                window.ui_language.setCurrentIndex(window.ui_language.findData("ru"))
                self.application.processEvents()
                window.workspace = None
                window._show_system_status()
                information.assert_called_once_with(
                    window,
                    ui_text("health.storage_title", "ru"),
                    ui_text("health.storage_body", "ru"),
                )
                window.close()

    def test_each_new_language_switches_persists_fits_and_keeps_content_language(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            with (
                patch.dict(os.environ, {"KSI_STATE_DIRECTORY": str(state)}),
                patch.object(MainWindow, "_initialize_workspace", return_value=None),
            ):
                window = MainWindow()
                content_language = window.language.currentData()
                for language in ("es", "de", "fr", "it", "zh"):
                    with self.subTest(language=language):
                        window.ui_language.setCurrentIndex(
                            window.ui_language.findData(language)
                        )
                        window.resize(820, 860)
                        window.show()
                        self.application.processEvents()
                        self.assertEqual(window.preferences.ui_language, language)
                        self.assertEqual(window.language.currentData(), content_language)
                        self.assertEqual(
                            window.centralWidget().horizontalScrollBar().maximum(), 0
                        )
                        self.assertTrue(window.start_button.accessibleName())
                        self.assertTrue(window.history_search.accessibleName())
                        self.assertTrue(window.history_copy_shortcut.isEnabled())
                self.assertEqual(load_preferences().ui_language, "zh")
                window.close()

    def test_core_error_is_redacted_and_localized_without_rewriting_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            with (
                patch.dict(os.environ, {"KSI_STATE_DIRECTORY": str(state)}),
                patch.object(MainWindow, "_initialize_workspace", return_value=None),
                patch.object(QMessageBox, "critical") as critical,
            ):
                window = MainWindow()
                window.ui_language.setCurrentIndex(window.ui_language.findData("ru"))
                window._failed(
                    "download failed at https://example.test/video?token=secret-value"
                )
                title, message = critical.call_args.args[1:]
                self.assertEqual(title, "Не удалось завершить обработку")
                self.assertIn("источник", message.casefold())
                self.assertIn("Действие:", message)
                self.assertNotIn("Yapılacak:", message)
                self.assertNotIn("secret-value", message)
                self.assertNotIn("example.test", message)
                diagnostic = window.log.toPlainText()
                self.assertIn("[gizlendi]", diagnostic)
                self.assertNotIn("secret-value", diagnostic)
                window.close()

    def test_each_new_language_localizes_the_basic_first_run_dialog(self) -> None:
        report = SimpleNamespace(
            passed=True,
            workspace="/Volumes/Test/KSI-Workspace",
            workspace_free_bytes=64 * 1024**3,
            tool_integrity_ok=True,
            health=SimpleNamespace(youtube_js_ready=True),
            models=(SimpleNamespace(ready=True),),
            app_bundle=SimpleNamespace(
                exists=True,
                signed=True,
                native_arm64_only=True,
                native_architecture_matches_host=True,
            ),
        )
        for language in ("es", "de", "fr", "it", "zh"):
            with self.subTest(language=language):
                dialog = FirstRunWizard(report, language=language)
                self.assertEqual(dialog.windowTitle(), ui_text("first.title", language))
                buttons = dialog.findChild(QDialogButtonBox)
                assert buttons is not None
                self.assertEqual(
                    buttons.button(QDialogButtonBox.StandardButton.Ok).text(),
                    ui_text("first.use", language),
                )
                dialog.close()


if __name__ == "__main__":
    unittest.main()
