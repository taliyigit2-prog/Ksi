"""Functional media/image forms backed by the shared local tools controller."""

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QListWidget, QMessageBox, QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

from ksi_local.media_tools import MediaRequest
from ksi_local.ui.components import Card, DropZone


class ToolPage(QWidget):
    def __init__(self, controller, *, images=False):
        super().__init__()
        self.controller = controller
        self.images = images
        self.sources = []
        self.last_output = None
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(30, 28, 30, 36)
        self.body.setSpacing(18)
        self.heading = QLabel("Görseller" if images else "Video ve Ses Araçları")
        self.heading.setObjectName("pageHeading")
        self.intro = QLabel("Özgün dosyalar korunur; bütün işlemler bu Mac'te yapılır.")
        self.intro.setObjectName("pageIntro")
        self.intro.setWordWrap(True)
        self.body.addWidget(self.heading)
        self.body.addWidget(self.intro)
        self.drop = DropZone(caption="Dosyaları buraya sürükle ve bırak", button_text="Dosya seç")
        self.drop.filesSelected.connect(self._select)
        self.body.addWidget(self.drop)
        self.file_list = QListWidget()
        self.file_list.setMaximumHeight(95)
        self.file_list.setAccessibleName("Seçilen dosyalar")
        self.body.addWidget(self.file_list)
        card = Card()
        form = QFormLayout()
        self.operation = QComboBox()
        choices = (("Biçim dönüştür", "convert_image"), ("PNG kayıpsız optimize et", "optimize_png"), ("AI arka planı kaldır", "remove_background")) if images else (("Dönüştür / sıkıştır", "convert"), ("Kes", "trim"), ("Parçaları birleştir", "join"), ("Kayıpsız kap değiştir", "remux"), ("Kalıcı altyazı ekle", "burn_subtitle"))
        for title, data in choices:
            self.operation.addItem(title, data)
        self.operation.currentIndexChanged.connect(self._operation_changed)
        self.format = QComboBox()
        self.format.addItems(("png", "jpg", "webp", "avif", "gif") if images else ("mp4", "mkv", "mov", "webm", "mp3", "wav", "flac", "aac", "gif"))
        self.profile = QComboBox()
        for title, data in (("Paylaşım · 1080p", "share"), ("Küçük dosya · 720p", "small"), ("Yüksek kalite", "archive")):
            self.profile.addItem(title, data)
        self.start_time = QDoubleSpinBox()
        self.end_time = QDoubleSpinBox()
        for control in (self.start_time, self.end_time):
            control.setRange(0, 10800)
            control.setSuffix(" sn")
            control.setDecimals(2)
        self.end_time.setSpecialValueText("Dosya sonu")
        self.lossless = QCheckBox("Kayıpsız hızlı mod (anahtar kareye bağlı)")
        self.strip = QCheckBox("Metadata bilgilerini kaldır")
        self.subtitle = QPushButton("Altyazı dosyası seç")
        self.subtitle_path = None
        self.subtitle.clicked.connect(self._choose_subtitle)
        self.precision = QLabel()
        self.precision.setObjectName("mutedLabel")
        self.precision.setWordWrap(True)
        form.addRow("İşlem", self.operation)
        form.addRow("Biçim", self.format)
        if not images:
            form.addRow("Profil", self.profile)
            form.addRow("Başlangıç", self.start_time)
            form.addRow("Bitiş", self.end_time)
            form.addRow("Kesme modu", self.lossless)
            form.addRow("Altyazı", self.subtitle)
        form.addRow("Gizlilik", self.strip)
        card.body.addLayout(form)
        card.body.addWidget(self.precision)
        self.body.addWidget(card)
        actions = QHBoxLayout()
        self.start_button = QPushButton("İşlemi başlat")
        self.start_button.setProperty("primary", True)
        self.start_button.clicked.connect(self._start)
        self.cancel_button = QPushButton("Durdur")
        self.cancel_button.clicked.connect(controller.cancel)
        self.open_button = QPushButton("Çıktıyı göster")
        self.open_button.clicked.connect(self._open)
        self.open_button.setEnabled(False)
        actions.addWidget(self.start_button)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.open_button)
        actions.addStretch()
        self.body.addLayout(actions)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.body.addWidget(self.progress)
        self.body.addWidget(self.status)
        self.body.addStretch()
        controller.progress.connect(lambda value: self.progress.setValue(round(value * 100)) if self._active() else None)
        controller.status.connect(lambda value: self.status.setText(value) if self._active() else None)
        controller.failed.connect(lambda value: self.status.setText(value) if self._active() else None)
        controller.result.connect(self._result)
        controller.finished.connect(self._finished)
        controller.busyChanged.connect(self._busy_changed)
        self._operation_changed()
        self.start_button.setEnabled(False)
        self.cancel_button.setEnabled(False)

    def _select(self, files):
        if self.controller.busy:
            return
        valid = [str(Path(path).expanduser().resolve()) for path in files if Path(path).is_file() and not Path(path).is_symlink()]
        self.sources = list(dict.fromkeys(valid))[:100]
        self.file_list.clear()
        self.file_list.addItems([Path(path).name for path in self.sources])
        self.start_button.setEnabled(bool(self.sources))

    def _operation_changed(self):
        operation = self.operation.currentData()
        for widget in (self.start_time, self.end_time):
            widget.setEnabled(operation == "trim")
        self.lossless.setEnabled(operation in {"trim", "join", "remux"})
        self.subtitle.setEnabled(operation == "burn_subtitle")
        if operation == "remux":
            self.lossless.setChecked(True)
        if self.images and operation in {"optimize_png", "remove_background"}:
            self.format.setCurrentText("png")
            self.format.setEnabled(False)
        else:
            self.format.setEnabled(True)
        self.precision.setText("Kayıpsız kesme anahtar karelere bağlıdır. Hassas kesme yeniden kodlar." if operation == "trim" else "Yeni çıktı iş klasörüne kaydedilir; kaynak dosyanın üzerine yazılmaz.")

    def _choose_subtitle(self):
        path, _ = QFileDialog.getOpenFileName(self, "Altyazı seç", "", "SRT (*.srt)")
        if path:
            self.subtitle_path = path
            self.subtitle.setText(Path(path).name)

    def _start(self):
        if not self.sources:
            return
        try:
            operation = self.operation.currentData()
            suffix = str(self.format.currentText())
            workspace = self.controller._service().workspace
            if self.images:
                requests = [{"operation": operation, "source": source,
                             "destination": str(workspace.outputs / (Path(source).stem + "-ksi." + suffix)),
                             "strip_metadata": self.strip.isChecked()} for source in self.sources]
                self.controller.start_images(requests)
            else:
                groups = [tuple(self.sources)] if operation == "join" else [(source,) for source in self.sources]
                requests = [MediaRequest(
                    sources=tuple(group), destination=str(workspace.outputs / (Path(group[0]).stem + "-ksi." + suffix)),
                    operation=operation, profile=self.profile.currentData(), start=self.start_time.value() if operation == "trim" else 0,
                    end=(self.end_time.value() or None) if operation == "trim" else None,
                    lossless=self.lossless.isChecked() if operation in {"trim", "join", "remux"} else False,
                    subtitle=self.subtitle_path if operation == "burn_subtitle" else None, strip_metadata=self.strip.isChecked(),
                ) for group in groups]
                self.controller.start_media(requests)
            self.start_button.setEnabled(False)
            self.cancel_button.setEnabled(True)
            self.progress.setValue(0)
        except (OSError, RuntimeError, ValueError) as error:
            QMessageBox.warning(self, "İşlem başlatılamadı", str(error))

    def _result(self, result):
        if not self._active():
            return
        self.last_output = result.get("output")
        self.open_button.setEnabled(bool(self.last_output))
        warnings = "\n".join(result.get("warnings", ()))
        self.status.setText("Tamamlandı." + ("\n" + warnings if warnings else ""))

    def _finished(self):
        self.start_button.setEnabled(bool(self.sources))
        self.cancel_button.setEnabled(False)

    def _active(self):
        return self.controller.active_kind == ("image" if self.images else "media")

    def _busy_changed(self, busy):
        self.start_button.setEnabled(not busy and bool(self.sources))
        self.cancel_button.setEnabled(busy and self._active())
        for control in (self.drop, self.operation, self.format, self.profile, self.start_time, self.end_time, self.lossless, self.strip, self.subtitle):
            control.setEnabled(not busy)
        if not busy:
            self._operation_changed()

    def _open(self):
        if self.last_output and Path(self.last_output).is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.last_output).parent)))
