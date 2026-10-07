"""Internal storage status; the default application-data location stays private."""

import shutil
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QLabel, QMessageBox, QPushButton

from ksi_local.ui.components import Card
from ksi_local.ui.strings import text
from ksi_local.project_metadata import WORKSPACE_DIRECTORY
from ksi_local.privacy import redact_sensitive_text
from ksi_local.workspace_selection import change_workspace


class StorageCard(Card):
    inspected = Signal(object)
    changed = Signal(object)

    def __init__(self, window):
        super().__init__()
        self.window = window
        self._busy = False
        self._root = None
        self._value = None
        self._changing = False
        self.title = QLabel()
        self.title.setObjectName("sectionLabel")
        self.path = QLabel()
        self.path.setTextFormat(Qt.TextFormat.PlainText)
        self.path.setWordWrap(True)
        self.space = QLabel()
        self.space.setObjectName("mutedLabel")
        self.open_button = QPushButton()
        self.open_button.clicked.connect(self._open)
        self.open_button.setEnabled(False)
        self.choose_button = QPushButton()
        self.choose_button.clicked.connect(self._choose)
        for widget in (self.title, self.path, self.space, self.open_button, self.choose_button):
            self.body.addWidget(widget)
        # Working data no longer needs a location picker or removable disk.
        # Keep the internal-only migration method for compatibility, not as a
        # second setup step in the normal settings UI.
        self.choose_button.hide()
        self.inspected.connect(self._ready)
        self.changed.connect(self._changed)
        self.timer = QTimer(self)
        self.timer.setInterval(5000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.retranslate()

    def refresh(self):
        if self._busy or self._changing or self.window.closing:
            return
        workspace = self.window.workspace
        if workspace is None:
            self._ready(None)
            return
        root = workspace.root
        self._busy = True

        def work():
            try:
                value = (root, shutil.disk_usage(root)) if root.is_dir() and not root.is_symlink() else None
            except OSError:
                value = None
            try:
                self.inspected.emit(value)
            except RuntimeError:
                pass

        threading.Thread(target=work, name="KSI-storage-status", daemon=True).start()

    def _ready(self, value):
        self._busy = False
        current = self.window.workspace
        if value is not None and (current is None or current.root != value[0]):
            value = None
        self._value = value
        self._root = value[0] if value else None
        self.open_button.setEnabled(self._root is not None)
        self.retranslate()

    def retranslate(self):
        language = self.window.preferences.ui_language
        self.title.setText(text("storage", language))
        self.open_button.setText(text("open_workspace", language))
        self.choose_button.setText(text("choose_workspace", language))
        self.choose_button.setEnabled(not self._changing)
        self.path.setText(str(self._root) if self._root else "—")
        if self._value:
            usage = self._value[1]
            self.space.setText(f"{self.window._format_model_bytes(usage.free)} / {self.window._format_model_bytes(usage.total)}")
        else:
            self.space.setText(self.window._t("system.waiting"))

    def _open(self):
        if self._root is not None and self._root.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._root)))

    def _choose(self):
        window = self.window
        controller = getattr(window, "tool_controller", None)
        language = window.preferences.ui_language
        if window._process_is_running() or (controller is not None and controller.busy) or window.preflight_pending or window.maintenance_pending or window.workspace_resolution_pending:
            QMessageBox.information(self, text("storage", language), text("workspace_busy", language))
            return
        selected = QFileDialog.getExistingDirectory(self, text("choose_workspace", language), str(self._root or Path.home()))
        if not selected:
            return
        parent = Path(selected)
        target = parent if (parent / ".workspace-id").is_file() else parent / WORKSPACE_DIRECTORY
        answer = QMessageBox.question(self, text("choose_workspace", language), text("workspace_choice_notice", language) + "\n\n" + str(target), QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._changing = True
        window.maintenance_pending = True
        window.workspace_timer.stop()
        window.start_button.setEnabled(False)
        self.retranslate()
        window.status.setText(text("workspace_changing", language))
        def work():
            try:
                change_workspace(target)
                error = None
            except (OSError, RuntimeError, ValueError) as failure:
                error = redact_sensitive_text(str(failure))
            try:
                self.changed.emit(error)
            except RuntimeError:
                pass
        threading.Thread(target=work, name="KSI-workspace-selection", daemon=True).start()

    def _changed(self, error):
        self._changing = False
        window = self.window
        window.maintenance_pending = False
        window.workspace_timer.start()
        self.retranslate()
        if error is not None:
            window.status.setText(error)
            if window.workspace is not None:
                window.start_button.setEnabled(True)
            return
        # Do not auto-resume old-root jobs when an explicit location changes.
        window.pending.clear()
        window.startup_interrupted_job_ids = ()
        window.workspace = None
        window.core.set_workspace(None)
        window._load_workspace()
