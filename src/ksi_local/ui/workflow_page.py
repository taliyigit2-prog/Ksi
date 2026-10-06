"""Recompose existing workflow controls into a native Halite-style page."""

from PySide6.QtWidgets import QFormLayout, QHBoxLayout, QLabel, QMessageBox, QPushButton

from ksi_local.ui.components import Card, DropZone
from ksi_local.ui.strings import text


class WorkflowPage:
    def __init__(self, window):
        self.window = window
        self.mode = "video"
        body = window.new_job_tab.layout()
        body.removeWidget(window.new_job_group)
        window.new_job_group.hide()
        while body.count():
            body.takeAt(0)
        body.setContentsMargins(30, 28, 30, 36)
        body.setSpacing(18)
        self.heading = QLabel()
        self.heading.setObjectName("pageHeading")
        self.intro = QLabel()
        self.intro.setObjectName("pageIntro")
        self.intro.setWordWrap(True)
        body.addWidget(self.heading)
        body.addWidget(self.intro)
        self.drop = DropZone(caption="", button_text="")
        self.drop.filesSelected.connect(self._files)
        body.addWidget(self.drop)
        source = Card()
        source.body.addWidget(window.source_label)
        row = QHBoxLayout()
        row.addWidget(window.source, 1)
        row.addWidget(window.browse_button)
        source.body.addLayout(row)
        body.addWidget(source)
        options = Card()
        form = QFormLayout()
        form.addRow(window._t("label.source_language"), window.language)
        for label, field in (
            (window.document_glossary_title, window.document_glossary_widget),
            (window.document_summary_profile_title, window.document_summary_profile),
            (window.document_summary_source_title, window.document_summary_source),
            (window.document_summary_pdf_title, window.document_summary_pdf),
        ):
            form.addRow(label, field)
        window.options_form = form
        options.body.addLayout(form)
        options.body.addWidget(window.advanced_session_toggle)
        options.body.addWidget(window.advanced_session_panel)
        options.body.addWidget(window.output_group)
        body.addWidget(options)
        for widget in (window.source_label, window.source, window.browse_button,
                       window.language, window.advanced_session_toggle, window.output_group,
                       window.start_button):
            widget.show()
        self.actions = QHBoxLayout()
        self.actions.addWidget(window.start_button)
        self.tools = QPushButton()
        self.tools.clicked.connect(lambda: window.tabs.setCurrentIndex(5))
        self.actions.addWidget(self.tools)
        self.actions.addStretch()
        body.addLayout(self.actions)
        body.addStretch(1)
        self.tools.setVisible(True)
        self.retranslate()

    def _files(self, files):
        if len(files) != 1:
            QMessageBox.information(self.window, text(self.mode, self.window.preferences.ui_language), text("single", self.window.preferences.ui_language))
            return
        self.window.source.setText(files[0])

    def select(self, mode):
        if mode not in {"download", "video", "document"}:
            raise ValueError("Bilinmeyen iş ekranı.")
        self.mode = mode
        kind = "document" if mode == "document" else "video"
        self.window._select_kind_card(kind, True)
        self.window.download_only.setChecked(mode == "download")
        self.window.output_group.setVisible(mode != "download")
        self.tools.setVisible(mode == "video")
        self.window.tabs.setCurrentIndex(0)
        self.retranslate()

    def retranslate(self):
        language = self.window.preferences.ui_language
        self.heading.setText(text(self.mode, language))
        self.intro.setText(text(f"{self.mode}_hint", language))
        self.drop.caption.setText(text("drop", language))
        self.drop.choose.setText(text("choose", language))
        self.tools.setText(text("media_tools", language))
