import unittest
import os
import tempfile
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ksi_local.i18n import SUPPORTED_UI_LANGUAGES
from ksi_local.ui.strings import model_description, validate_catalog


class ModelUiStringsTests(unittest.TestCase):
    def test_bundled_model_descriptions_are_localized_in_every_ui_language(self):
        validate_catalog()
        for identifier in ("translategemma", "qwen3.5", "whisper", "chatterbox", "piper", "u2netp", "argos-en-tr", "argos-tr-en"):
            english = model_description(identifier, "upstream fallback", "en")
            for language in SUPPORTED_UI_LANGUAGES:
                with self.subTest(model=identifier, language=language):
                    result = model_description(identifier, "upstream fallback", language)
                    self.assertTrue(result)
                    self.assertNotEqual(result, "upstream fallback")
                    if language != "en":
                        self.assertNotEqual(result, english)

    def test_unknown_model_description_is_not_invented(self):
        self.assertEqual(model_description("future-model", "Original upstream description", "tr"), "Original upstream description")

    def test_existing_model_rows_follow_live_interface_language_changes(self):
        from PySide6.QtWidgets import QApplication, QLabel
        from ksi_local.gui import MainWindow
        from ksi_local.model_manager import ModelInventory
        application = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory, \
             patch.dict(os.environ, {"KSI_STATE_DIRECTORY": directory}), \
             patch.object(MainWindow, "_initialize_workspace", return_value=None):
            window = MainWindow()
            model = ModelInventory("whisper", "Whisper", "Original upstream fallback", "MIT", 100, 100, "installed")
            window.studio_shell._models_ready((model,))
            for locale in ("en", "tr"):
                window.ui_language.setCurrentIndex(window.ui_language.findData(locale))
                application.processEvents()
                row = window.studio_shell.model_list.itemAt(0).widget()
                labels = "\n".join(label.text() for label in row.findChildren(QLabel))
                self.assertIn(model_description("whisper", model.description, locale), labels)
            window.close()
