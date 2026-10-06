"""Offline catalog browsing; opening a website is an explicit button action."""

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QLabel, QLineEdit, QListWidget, QPushButton, QVBoxLayout, QWidget

from ksi_local.catalog import search_public_catalog
from ksi_local.ui.strings import text


class LibraryPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.entries = ()
        body = QVBoxLayout(self)
        body.setContentsMargins(30, 28, 30, 36)
        body.setSpacing(18)
        self.heading = QLabel()
        self.heading.setObjectName("pageHeading")
        self.intro = QLabel()
        self.intro.setObjectName("pageIntro")
        self.intro.setWordWrap(True)
        self.search = QLineEdit()
        self.search.textChanged.connect(self._search)
        self.results = QListWidget()
        self.results.currentRowChanged.connect(lambda row: self.open_button.setEnabled(0 <= row < len(self.entries)))
        self.open_button = QPushButton()
        self.open_button.clicked.connect(self._open)
        self.open_button.setEnabled(False)
        for widget in (self.heading, self.intro, self.search, self.results, self.open_button):
            body.addWidget(widget)
        self.retranslate()

    def _search(self, query):
        try:
            self.entries = search_public_catalog(query)
        except (OSError, ValueError):
            self.entries = ()
        self.results.clear()
        language = self.window.preferences.ui_language
        for entry in self.entries:
            title = entry.title_tr if language == "tr" else entry.title_ru if language == "ru" else entry.title_en
            self.results.addItem(f"{title}\n{entry.license} · {entry.content_type}")
        self.open_button.setEnabled(False)

    def _open(self):
        row = self.results.currentRow()
        if 0 <= row < len(self.entries):
            QDesktopServices.openUrl(QUrl(self.entries[row].url))

    def retranslate(self):
        language = self.window.preferences.ui_language
        self.heading.setText(text("library", language))
        self.intro.setText(text("library_hint", language))
        self.search.setPlaceholderText(text("search", language))
        self.open_button.setText(text("open_resource", language))
        self._search(self.search.text())
