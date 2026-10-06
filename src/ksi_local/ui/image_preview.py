"""Bounded image thumbnails off the GUI thread; never reads a remote URL."""

import threading
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QLabel

from ksi_local.ui.strings import text


class ImagePreview(QLabel):
    ready = Signal(int, object)

    def __init__(self, window):
        super().__init__()
        self.window = window
        self.generation = 0
        self._image = None
        self._busy = False
        self._pending = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(150)
        self.setMaximumHeight(220)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setProperty("card", True)
        self.ready.connect(self._ready)
        self.retranslate()

    def load(self, filename):
        self.generation += 1
        generation = self.generation
        path = Path(filename) if filename else None
        self._pending = (generation, path) if path else None
        self._image = None
        self.clear()
        self.retranslate()
        if path is None or self._busy:
            return
        self._launch()

    def _launch(self):
        generation, path = self._pending
        self._pending = None
        self._busy = True

        def work():
            from PIL import Image, ImageOps

            result = None
            try:
                if path.is_symlink() or not path.is_file() or path.stat().st_size > 64 * 1024**2:
                    raise ValueError("Thumbnail input exceeds its bounded scope")
                with Image.open(path) as source:
                    if source.width * source.height > 25_000_000:
                        raise ValueError("Thumbnail pixel budget exceeded")
                    image = ImageOps.exif_transpose(source)
                    image.thumbnail((640, 360))
                    image = image.convert("RGBA")
                    pixels = image.tobytes()
                    result = QImage(pixels, image.width, image.height, image.width * 4,
                                    QImage.Format.Format_RGBA8888).copy()
            except (OSError, ValueError, Image.DecompressionBombError):
                pass
            try:
                self.ready.emit(generation, result)
            except RuntimeError:
                pass

        threading.Thread(target=work, name="KSI-image-preview", daemon=True).start()

    def _ready(self, generation, image):
        self._busy = False
        if self.window.closing:
            return
        if generation == self.generation:
            self._image = image
            self._render()
        if self._pending is not None:
            self._launch()

    def _render(self):
        if self._image is None or self._image.isNull():
            self.retranslate()
        else:
            pixmap = QPixmap.fromImage(self._image)
            self.setPixmap(pixmap.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._image is not None:
            self._render()

    def retranslate(self):
        self.setAccessibleName(text("preview", self.window.preferences.ui_language))
        if self._image is None:
            self.setText(text("preview_hint", self.window.preferences.ui_language))
