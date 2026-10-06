from __future__ import annotations

import os
import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QApplication, QScrollArea

from ksi_local.gui import DropLineEdit, MainWindow, _actionable_message
from ksi_local.job_store import JobKind


ROOT = Path(__file__).resolve().parents[1]
TEST_APP = QApplication.instance() or QApplication([])


class PhaseEighteenGuiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.state = patch.dict(
            os.environ,
            {"KSI_STATE_DIRECTORY": str(Path(self.temporary.name) / "state")},
        )
        self.state.start()
        self.initializer = patch.object(MainWindow, "_initialize_workspace", return_value=None)
        self.initializer.start()
        self.window = MainWindow()

    def tearDown(self) -> None:
        self.window.close()
        self.initializer.stop()
        self.state.stop()
        self.temporary.cleanup()

    def test_simple_cards_and_advanced_sections(self) -> None:
        self.assertIsInstance(self.window.centralWidget(), QScrollArea)
        self.assertIn("Video", self.window.video_card.accessibleName())
        self.assertIn("Belge", self.window.document_card.accessibleName())
        self.assertTrue(self.window.advanced_session_panel.isHidden())
        self.assertTrue(self.window.external_review_panel.isHidden())
        self.assertTrue(self.window.job_kind.isHidden())

        self.window.document_card.click()
        self.assertEqual(self.window.job_kind.currentData(), "document")
        self.assertTrue(self.window.document_card.isChecked())
        self.assertEqual(self.window.want_subtitle.text(), "Belgeyi Türkçeye çevir")
        self.assertFalse(self.window.download_only.isEnabled())
        self.assertFalse(self.window.want_dub.isEnabled())

    def test_local_file_drop_places_the_path_in_the_source_field(self) -> None:
        field = DropLineEdit()
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile("/tmp/ksi_local-surukle-birak.txt")])
        event = QDropEvent(
            QPointF(10, 10),
            Qt.DropAction.CopyAction,
            mime,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        field.dropEvent(event)
        self.assertTrue(event.isAccepted())
        self.assertEqual(field.text(), "/tmp/ksi_local-surukle-birak.txt")

    def test_small_window_scrolls_and_standard_window_does_not_overflow_horizontally(self) -> None:
        scroll = self.window.centralWidget()
        assert isinstance(scroll, QScrollArea)
        self.window.resize(820, 860)
        self.window.show()
        TEST_APP.processEvents()
        self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)

        self.window.resize(620, 560)
        TEST_APP.processEvents()
        self.assertGreater(scroll.verticalScrollBar().maximum(), 0)

    def test_accessibility_focus_and_palette_aware_colors(self) -> None:
        required = (
            self.window.source,
            self.window.language,
            self.window.start_button,
            self.window.status,
            self.window.progress,
            self.window.history,
            self.window.export_button,
            self.window.system_status_button,
        )
        self.assertTrue(all(widget.accessibleName() for widget in required))
        self.assertIs(self.window.video_card.nextInFocusChain(), self.window.document_card)
        style = self.window.styleSheet()
        self.assertIn("palette(alternate-base)", style)
        self.assertIn("palette(midlight)", style)
        self.assertNotIn("#9a6700", style)
        self.assertNotIn("#b42318", style)

    def test_running_job_stop_control_is_prominent_and_accessible(self) -> None:
        self.assertEqual(self.window.cancel_button.text(), "■  Çalışan İşi Durdur")
        self.assertEqual(self.window.cancel_button.objectName(), "stopButton")
        self.assertEqual(
            self.window.cancel_button.accessibleName(), "Çalışan işi durdur"
        )
        self.assertIn("devam noktaları korunur", self.window.cancel_button.toolTip())
        self.assertFalse(self.window.cancel_button.isEnabled())
        self.assertGreaterEqual(self.window.cancel_button.minimumHeight(), 38)

    def test_history_shows_kind_language_outputs_status_and_storage(self) -> None:
        root = Path(self.temporary.name)
        job_directory = root / "KSI-Workspace/jobs/DOC-18"
        job_directory.mkdir(parents=True)
        self.window.store.create_job(
            job_id="DOC-18",
            source=str(root / "ornek.pdf"),
            source_language="es",
            want_subtitle=True,
            want_summary=True,
            want_dub=False,
            job_directory=job_directory,
            job_kind=JobKind.DOCUMENT,
        )
        self.window._refresh_history()
        self.assertEqual(self.window.history.columnCount(), 6)
        self.assertEqual(self.window.history.item(0, 2).text(), "Belge · İspanyolca · SSD")
        self.assertEqual(self.window.history.item(0, 3).text(), "Çeviri, Özet")
        self.assertEqual(self.window.history.item(0, 4).text(), "Kuyrukta")
        self.assertIn("Depolama:", self.window.job_context_label.text())

    def test_error_message_always_has_one_clear_action(self) -> None:
        message = _actionable_message("Disk okunamadı.", "SSD'yi yeniden bağlayın.")
        self.assertEqual(message.count("Yapılacak:"), 1)
        self.assertIn("SSD'yi yeniden bağlayın", message)
        self.assertEqual(_actionable_message(message, "Tekrar deneyin."), message)


class PhaseEighteenIconTests(unittest.TestCase):
    def test_logo_and_icns_contain_required_readable_sizes(self) -> None:
        with Image.open(ROOT / "assets/ksi-logo.png") as source_logo:
            logo = source_logo.convert("RGBA")
        self.assertEqual(logo.size, (1024, 1024))
        opaque = [pixel for pixel in logo.get_flattened_data() if pixel[3] > 0]
        self.assertGreater(len(set(opaque)), 100)

        with Image.open(ROOT / "packaging/KSI-Local-Studio.icns") as icon:
            icon_sizes = tuple(icon.info.get("sizes", []))
        logical_sizes = {item[0] for item in icon_sizes}
        pixel_sizes = {item[0] * item[2] for item in icon_sizes}
        for size in (16, 32, 128, 512, 1024):
            self.assertTrue(size in logical_sizes or size in pixel_sizes)
            preview = logo.resize((size, size))
            self.assertGreater(len(set(preview.get_flattened_data())), 8)
        logo.close()

        with (ROOT / "packaging/Info.plist").open("rb") as handle:
            plist = plistlib.load(handle)
        self.assertEqual(plist["CFBundleIconFile"], "KSI-Local-Studio.icns")


if __name__ == "__main__":
    unittest.main()
