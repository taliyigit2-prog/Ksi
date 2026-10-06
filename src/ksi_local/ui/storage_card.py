"""Read-only storage status; disconnected volumes never get a fallback path."""

import shutil
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QLabel, QPushButton

from ksi_local.ui.components import Card
from ksi_local.ui.strings import text


class StorageCard(Card):
    inspected = Signal(object)

    def __init__(self, window):
        super().__init__()
        self.window = window
        self._busy = False
        self._root = None
        self._value = None
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
        for widget in (self.title, self.path, self.space, self.open_button):
            self.body.addWidget(widget)
        self.inspected.connect(self._ready)
        self.timer = QTimer(self)
        self.timer.setInterval(5000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.retranslate()

    def refresh(self):
        if self._busy or self.window.closing:
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
        self.path.setText(str(self._root) if self._root else "—")
        if self._value:
            usage = self._value[1]
            self.space.setText(f"{self.window._format_model_bytes(usage.free)} / {self.window._format_model_bytes(usage.total)}")
        else:
            self.space.setText(self.window._t("system.waiting"))

    def _open(self):
        if self._root is not None and self._root.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._root)))
