"""Native sidebar and Halite-style settings over the existing service widgets."""

import platform
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from ksi_local import __version__
from ksi_local.ui.components import Card, ModelRow
from ksi_local.ui.strings import text as studio_text
from ksi_local.preferences import save_preferences


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
        navigation.setContentsMargins(12, 18, 12, 12)
        navigation.setSpacing(6)
        brand = QHBoxLayout()
        brand.setContentsMargins(8, 6, 0, 12)
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
        from ksi_local.ui.tool_controller import ToolController
        from ksi_local.ui.tool_pages import ToolPage

        window.tool_controller = ToolController(window)
        window.media_tools_page = ToolPage(window.tool_controller)
        window.image_tools_page = ToolPage(window.tool_controller, images=True)
        for page, name in ((window.media_tools_page, "Video ve Ses"), (window.image_tools_page, "Görseller")):
            index = window.tabs.addTab(page, name)
            button = QPushButton(name)
            button.setProperty("nav", True)
            button.setCheckable(True)
            button.setObjectName(f"nav-{index}")
            button.clicked.connect(lambda checked, selected=index: window.tabs.setCurrentIndex(selected))
            self.group.addButton(button, index)
            self.buttons.append(button)
        from ksi_local.ui.workflow_page import WorkflowPage
        from ksi_local.ui.library_page import LibraryPage

        window.workflow_page = WorkflowPage(window)
        window.library_page = LibraryPage(window)
        window.tabs.addTab(window.library_page, "Library")
        self.routes = {"download": 0, "video": 8, "document": 9, "images": 6,
                       "queue": 1, "history": 2, "library": 7, "help": 4, "settings": 3}
        for index, route in ((7, "library"), (8, "video"), (9, "document")):
            button = QPushButton()
            button.setProperty("nav", True)
            button.setCheckable(True)
            button.clicked.connect(lambda checked, selected=route: self._navigate(selected))
            self.group.addButton(button, index)
            self.buttons.append(button)
        self.buttons[0].clicked.disconnect()
        self.buttons[0].clicked.connect(lambda checked: self._navigate("download"))
        for route, index in self.routes.items():
            self.buttons[index].setObjectName(f"nav-{route}")
        self.buttons[5].setParent(self.sidebar)
        self.buttons[5].hide()
        for index in (0, 8, 9, 6, 1, 2, 7):
            navigation.addWidget(self.buttons[index])
        navigation.addStretch(1)
        navigation.addWidget(self.buttons[4])
        navigation.addWidget(self.buttons[3])
        window.tabs.currentChanged.connect(self._selected)
        self._selected(window.tabs.currentIndex())
        row.addWidget(self.sidebar)
        content = QScrollArea()
        content.setWidgetResizable(True)
        content.setFrameShape(QFrame.Shape.NoFrame)
        content.setWidget(window.tabs)
        row.addWidget(content, 1)
        root_layout.addWidget(container, 1)
        self.container = container
        for index in range(window.tabs.count()):
            page_layout = window.tabs.widget(index).layout()
            if page_layout:
                page_layout.setContentsMargins(30, 28, 30, 36)
                page_layout.setSpacing(18)
        self._settings()
        from ksi_local.ui.model_controller import ModelController

        window.model_controller = ModelController(window)
        window.model_controller.inventoryReady.connect(self._models_ready)
        window.model_controller.failed.connect(self.model_status.setText)
        window.tabs.currentChanged.connect(lambda index: window.model_controller.refresh() if index == 3 else None)
        self.retranslate()

    def _selected(self, index):
        if index == 0:
            index = self.routes.get(self.window.workflow_page.mode, 8)
        elif index == 5:
            index = 8
        if 0 <= index < len(self.buttons):
            self.buttons[index].setChecked(True)

    def _navigate(self, route):
        if route in ("download", "video", "document"):
            self.window.workflow_page.select(route)
        else:
            self.window.tabs.setCurrentIndex(self.routes[route])
        self.buttons[self.routes[route]].setChecked(True)

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
        translation = Card()
        self.translation_label = QLabel()
        translation.body.addWidget(self.translation_label)
        self.translation_engine = QComboBox()
        self.translation_engine.addItem("Gemma · local", "gemma")
        self.translation_engine.addItem("Argos · CPU", "argos")
        self.translation_engine.setCurrentIndex(self.translation_engine.findData(window.preferences.translation_engine))
        self.translation_engine.currentIndexChanged.connect(self._translation_engine_changed)
        translation.body.addWidget(self.translation_engine)
        body.addWidget(translation)
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
        from ksi_local.ui.storage_card import StorageCard
        self.storage_card = StorageCard(window)
        body.addWidget(self.storage_card)
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
        glyphs = {"download": "↓", "video": "▷", "document": "▤", "images": "◇",
                  "queue": "≡", "history": "◷", "library": "▦", "help": "?", "settings": "⚙"}
        for route, index in self.routes.items():
            label = studio_text(route, self.window.preferences.ui_language)
            self.buttons[index].setText(f"{glyphs[route]}   {label}")
            self.buttons[index].setAccessibleName(label)
        self.window.workflow_page.retranslate()
        self.window.library_page.retranslate()
        self.window.media_tools_page.retranslate()
        self.window.image_tools_page.retranslate()
        self.storage_card.retranslate()
        self.window._set_combo_item_texts(self.window.history_filter, {
            "media": studio_text("video", self.window.preferences.ui_language),
            "image": studio_text("images", self.window.preferences.ui_language),
        })
        self.translation_label.setText(studio_text("translation", self.window.preferences.ui_language))
        self.window.system_heading.setText(labels[3])
        self.window.ui_language_label.setText(labels[5])
        self.window.theme_label.setText(labels[6])
        self.models_heading.setText(labels[7])
        self.about_heading.setText(labels[8])
        self.description.setText(labels[9])
        self.advanced.setText(labels[10])
        self.model_status.setText(self.window._t("system.waiting"))

    def _translation_engine_changed(self):
        selected = self.translation_engine.currentData()
        if self.window._process_is_running():
            blocked = self.translation_engine.blockSignals(True)
            self.translation_engine.setCurrentIndex(self.translation_engine.findData(self.window.preferences.translation_engine))
            self.translation_engine.blockSignals(blocked)
            return
        self.window.preferences = replace(self.window.preferences, translation_engine=selected)
        save_preferences(self.window.preferences)

    def _models_ready(self, rows):
        while self.model_list.count():
            item = self.model_list.takeAt(0)
            if item.widget() and item.widget() is not self.model_status:
                item.widget().deleteLater()
        language = self.window.preferences.ui_language
        words = {
            "tr": ("Kurulu", "Doğrulandı", "Eksik", "Bozuk", "Doğrula", "Paketten kur"),
            "en": ("Installed", "Verified", "Missing", "Corrupt", "Verify", "Install from bundle"),
            "ru": ("Установлено", "Проверено", "Отсутствует", "Повреждено", "Проверить", "Установить из пакета"),
            "es": ("Instalado", "Verificado", "Falta", "Dañado", "Verificar", "Instalar del paquete"),
            "de": ("Installiert", "Geprüft", "Fehlt", "Beschädigt", "Prüfen", "Aus Paket installieren"),
            "fr": ("Installé", "Vérifié", "Absent", "Corrompu", "Vérifier", "Installer du paquet"),
            "it": ("Installato", "Verificato", "Mancante", "Danneggiato", "Verifica", "Installa dal pacchetto"),
            "zh": ("已安装", "已验证", "缺失", "损坏", "验证", "从安装包安装"),
        }.get(language, ("Installed", "Verified", "Missing", "Corrupt", "Verify", "Install from bundle"))
        indices = {"installed": 0, "verified": 1, "missing": 2, "corrupt": 3}
        for model in rows:
            row = ModelRow(model.identifier, model.title, model.description + "\n" + model.license)
            row.set_status(badge=words[indices[model.state]], size=self.window._format_model_bytes(model.total_bytes), action=words[5] if model.state == "missing" else words[4])
            row.actionRequested.connect(lambda identifier, state=model.state: self.window.model_controller.refresh(verify=True, install=state == "missing"))
            self.model_list.addWidget(row)
        self.model_status.setVisible(not rows)
        self.model_list.addWidget(self.model_status)
