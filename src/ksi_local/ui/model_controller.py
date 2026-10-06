"""Background offline model inventory with Qt-only result delivery."""

import threading

from PySide6.QtCore import QObject, Signal

from ksi_local.bundle_runtime import bundle_root
from ksi_local.model_manager import ModelManager
from ksi_local.privacy import redact_sensitive_text


class ModelController(QObject):
    inventoryReady = Signal(object)
    failed = Signal(str)

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self._busy = False
        self.inventoryReady.connect(lambda _: self._finished())
        self.failed.connect(lambda _: self._finished())

    def refresh(self, *, verify=False, install=False):
        if self._busy:
            return
        workspace, resources = self.window.workspace, bundle_root()
        if workspace is None or resources is None:
            self.inventoryReady.emit(())
            return
        self._busy = True

        def work():
            try:
                manager = ModelManager(resources, workspace.root / "models")
                rows = manager.install() if install else manager.inventory(verify=verify)
                self.inventoryReady.emit(rows)
            except Exception as error:
                try:
                    self.failed.emit(redact_sensitive_text(str(error))[:1000])
                except RuntimeError:
                    pass

        threading.Thread(target=work, name="KSI-model-inventory", daemon=True).start()

    def _finished(self):
        self._busy = False
