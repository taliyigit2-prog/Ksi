"""Native sidebar and Halite-style settings over the existing service widgets."""

import platform
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from ksi_local import __version__
from ksi_local.ui.components import Card


LABELS = {
    "tr": ("İşlemler", "Kuyruk", "Geçmiş", "Ayarlar", "Yardım", "Dil", "Tema", "Yapay zekâ modelleri", "Hakkında", "Tamamen yerel medya stüdyosu", "Gelişmiş sistem bilgileri"),
    "en": ("Workflows", "Queue", "History", "Settings", "Help", "Language", "Theme", "AI models", "About", "Fully local media studio", "Advanced system information"),
    "ru": ("Задачи", "Очередь", "История", "Настройки", "Помощь", "Язык", "Тема", "Модели ИИ", "О программе", "Локальная медиастудия", "Системная информация"),
    "es": ("Procesos", "Cola", "Historial", "Ajustes", "Ayuda", "Idioma", "Tema", "Modelos de IA", "Acerca de", "Estudio multimedia local", "Información avanzada del sistema"),
    "de": ("Arbeitsabläufe", "Warteschlange", "Verlauf", "Einstellungen", "Hilfe", "Sprache", "Design", "KI-Modelle", "Über", "Lokales Medienstudio", "Erweiterte Systeminformationen"),
    "fr": ("Traitements", "File d’attente", "Historique", "Réglages", "Aide", "Langue", "Thème", "Modèles IA", "À propos", "Studio multimédia local", "Informations système avancées"),
    "it": ("Operazioni", "Coda", "Cronologia", "Impostazioni", "Aiuto", "Lingua", "Tema", "Modelli IA", "Informazioni", "Studio multimediale locale", "Informazioni di sistema avanzate"),
    "zh": ("工作流程", "队列", "历史记录", "设置", "帮助", "语言", "主题", "AI 模型", "关于", "完全本地的媒体工作室", "高级系统信息"),
}


def _hide_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().hide()
        elif item.layout():
            _hide_layout(item.layout())


class StudioShell:
    def __init__(self, window, root_layout, header_layout, logo: Path):
        self.window = window
        root_layout.removeItem(header_layout)
        _hide_layout(header_layout)
        root_layout.removeWidget(window.tabs)
        window.tabs.tabBar().hide()
        root_layout.setContentsMargins(0, 0, 0, 0)
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.sidebar = QWidget()
        self.sidebar.setObjectName("studioSidebar")
        self.sidebar.setFixedWidth(232)
        navigation = QVBoxLayout(self.sidebar)
        navigation.setContentsMargins(12, 26, 12, 18)
        navigation.setSpacing(8)
        brand = QHBoxLayout()
        brand.setContentsMargins(8, 10, 0, 18)
        icon = QLabel()
        icon.setFixedSize(40, 40)
        if logo.is_file():
            icon.setPixmap(QPixmap(str(logo)).scaled(40, 40, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        text = QVBoxLayout()
        name = QLabel("KSI Local Studio")
        name.setObjectName("brandTitle")
        self.description = QLabel()
        self.description.setObjectName("brandDescription")
        self.description.setWordWrap(True)
        text.addWidget(name)
        text.addWidget(self.description)
        brand.addWidget(icon)
        brand.addLayout(text)
        navigation.addLayout(brand)
        self.buttons = []
        self.group = QButtonGroup(self.sidebar)
        self.group.setExclusive(True)
        for index in range(5):
            button = QPushButton()
            button.setProperty("nav", True)
            button.setObjectName(f"nav-{index}")
            button.setCheckable(True)
            button.clicked.connect(lambda checked, selected=index: window.tabs.setCurrentIndex(selected))
            self.group.addButton(button, index)
            self.buttons.append(button)
        for index in (0, 1, 2):
            navigation.addWidget(self.buttons[index])
        navigation.addStretch(1)
        navigation.addWidget(self.buttons[4])
        navigation.addWidget(self.buttons[3])
        window.tabs.currentChanged.connect(self._selected)
        self._selected(window.tabs.currentIndex())
        row.addWidget(self.sidebar)
        row.addWidget(window.tabs, 1)
        root_layout.addWidget(container, 1)
        self.container = container
        for index in range(window.tabs.count()):
            page_layout = window.tabs.widget(index).layout()
            if page_layout:
                page_layout.setContentsMargins(30, 28, 30, 36)
                page_layout.setSpacing(18)
        self._settings()
        self.retranslate()

    def _selected(self, index):
        if 0 <= index < len(self.buttons):
            self.buttons[index].setChecked(True)

    def _settings(self):
        window = self.window
        body = window.system_tab.layout()
        _hide_layout(body)
        body.addWidget(window.system_heading)
        window.system_heading.show()
        preferences = Card()
        row = QHBoxLayout()
        for label, control in ((window.ui_language_label, window.ui_language), (window.theme_label, window.theme)):
            column = QVBoxLayout()
            column.addWidget(label)
            column.addWidget(control)
            control.setMinimumWidth(145)
            label.setObjectName("mutedLabel")
            label.show()
            control.show()
            row.addLayout(column)
        row.addStretch(1)
        preferences.body.addLayout(row)
        body.addWidget(preferences)
        self.models_heading = QLabel()
        self.models_heading.setObjectName("sectionLabel")
        body.addWidget(self.models_heading)
        self.model_list = QVBoxLayout()
        self.model_list.setSpacing(10)
        body.addLayout(self.model_list)
        self.model_status = QLabel()
        self.model_status.setObjectName("mutedLabel")
        self.model_status.setWordWrap(True)
        self.model_list.addWidget(self.model_status)
        self.about_heading = QLabel()
        self.about_heading.setObjectName("sectionLabel")
        body.addWidget(self.about_heading)
        about = Card()
        about.body.addWidget(QLabel(f"KSI Local Studio  v{__version__} · macOS ({platform.machine()})"))
        body.addWidget(about)
        self.advanced = QPushButton()
        self.advanced.setCheckable(True)
        body.addWidget(self.advanced)
        panel = Card()
        for widget in (window.system_status_button, window.system_full_verify_button, window.system_summary, window.external_review_toggle, window.external_review_panel):
            panel.body.addWidget(widget)
            if widget is not window.external_review_panel:
                widget.show()
        panel.hide()
        self.advanced.toggled.connect(panel.setVisible)
        body.addWidget(panel)
        body.addStretch(1)

    def retranslate(self):
        labels = LABELS.get(self.window.preferences.ui_language, LABELS["en"])
        glyphs = ("◈", "≡", "◷", "⚙", "?")
        for index, button in enumerate(self.buttons):
            button.setText(f"{glyphs[index]}   {labels[index]}")
            button.setAccessibleName(labels[index])
        self.window.system_heading.setText(labels[3])
        self.window.ui_language_label.setText(labels[5])
        self.window.theme_label.setText(labels[6])
        self.models_heading.setText(labels[7])
        self.about_heading.setText(labels[8])
        self.description.setText(labels[9])
        self.advanced.setText(labels[10])
        self.model_status.setText(self.window._t("system.waiting"))
