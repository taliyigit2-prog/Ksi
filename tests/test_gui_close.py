import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from shiboken6 import isValid
from ksi_local.gui import MainWindow


class GuiCloseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def test_read_only_health_check_does_not_trap_closing_in_a_modal(self):
        with tempfile.TemporaryDirectory() as temporary, \
             patch.dict(os.environ, {"KSI_STATE_DIRECTORY": temporary}), \
             patch.object(MainWindow, "_initialize_workspace", return_value=None):
            window = MainWindow()
            window.maintenance_pending = True
            window.health_status_pending = True
            event = MagicMock()
            with patch("ksi_local.gui.QMessageBox.information") as information:
                window.closeEvent(event)
                event.accept.assert_called_once()
                event.ignore.assert_not_called()
                information.assert_not_called()
                window._system_status_finished(None, RuntimeError("late diagnostic result"))
                information.assert_not_called()
            self.assertTrue(window.closing)
            self.assertFalse(window.maintenance_pending)
            window.close()

    def test_mutating_maintenance_is_deferred_without_a_blocking_modal(self):
        with tempfile.TemporaryDirectory() as temporary, \
             patch.dict(os.environ, {"KSI_STATE_DIRECTORY": temporary}), \
             patch.object(MainWindow, "_initialize_workspace", return_value=None):
            window = MainWindow()
            window.maintenance_pending = True
            event = MagicMock()
            with patch("ksi_local.gui.QMessageBox.information") as information, \
                 patch("ksi_local.gui.QTimer.singleShot") as later:
                window.closeEvent(event)
                event.ignore.assert_called_once()
                event.accept.assert_not_called()
                information.assert_not_called()
                later.assert_called_once_with(200, window.close)
            self.assertFalse(window.closing)
            window.maintenance_pending = False
            window.close()

    def test_accepted_close_disposes_native_widgets(self):
        with tempfile.TemporaryDirectory() as temporary, \
             patch.dict(os.environ, {"KSI_STATE_DIRECTORY": temporary}), \
             patch.object(MainWindow, "_initialize_workspace", return_value=None):
            window = MainWindow()
            self.assertTrue(window.testAttribute(Qt.WidgetAttribute.WA_DeleteOnClose))
            window.close()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.assertFalse(isValid(window))
