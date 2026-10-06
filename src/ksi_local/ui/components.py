"""Reusable native cards, drop zones and model status rows."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)


class Card(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setProperty("card", True)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(18, 18, 18, 18)
        self.body.setSpacing(12)


class DropZone(QFrame):
    filesSelected = Signal(list)

    def __init__(self, *, caption: str, button_text: str, file_filter: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("dropZone")
        self.setAcceptDrops(True)
        self.file_filter = file_filter
        self.setMinimumHeight(190)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 28, 24, 28)
        layout.setSpacing(14)
        self.icon_label = QLabel("↑")
        self.icon_label.setStyleSheet("font-size: 34px; color: palette(mid);")
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.caption = QLabel(caption)
        self.caption.setObjectName("mutedLabel")
        self.caption.setWordWrap(True)
        self.caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.choose = QPushButton(button_text)
        self.choose.setProperty("primary", True)
        self.choose.clicked.connect(self._choose)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self.choose)
        row.addStretch()
        layout.addWidget(self.icon_label)
        layout.addWidget(self.caption)
        layout.addLayout(row)

    def _choose(self):
        files, _ = QFileDialog.getOpenFileNames(self, self.choose.text(), "", self.file_filter)
        if files:
            self.filesSelected.emit(files)

    def _drag_style(self, active: bool):
        self.setProperty("dragActive", active)
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls()
        if urls and len(urls) <= 1000 and all(url.isLocalFile() for url in urls):
            event.acceptProposedAction()
            self._drag_style(True)
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self._drag_style(False)
        event.accept()

    def dropEvent(self, event):
        self._drag_style(False)
        urls = event.mimeData().urls()
        if urls and len(urls) <= 1000 and all(url.isLocalFile() for url in urls):
            self.filesSelected.emit([url.toLocalFile() for url in urls])
            event.acceptProposedAction()
        else:
            event.ignore()


class ModelRow(Card):
    actionRequested = Signal(str)

    def __init__(self, identifier: str, title: str, description: str, parent=None):
        super().__init__(parent)
        self.identifier = identifier
        row = QHBoxLayout()
        copy = QVBoxLayout()
        self.title = QLabel(title)
        self.title.setStyleSheet("font-weight: 600;")
        self.description = QLabel(description)
        self.description.setObjectName("mutedLabel")
        self.description.setWordWrap(True)
        copy.addWidget(self.title)
        copy.addWidget(self.description)
        self.size_label = QLabel("—")
        self.size_label.setObjectName("mutedLabel")
        self.badge = QLabel()
        self.badge.setObjectName("modelBadge")
        self.action = QPushButton()
        self.action.clicked.connect(lambda: self.actionRequested.emit(self.identifier))
        row.addLayout(copy, 1)
        row.addWidget(self.size_label)
        row.addWidget(self.badge)
        row.addWidget(self.action)
        self.body.addLayout(row)
        self.action.hide()

    def set_status(self, *, badge: str, size: str, action: str | None = None):
        self.badge.setText(badge)
        self.size_label.setText(size)
        self.action.setVisible(action is not None)
        self.action.setText(action or "")
