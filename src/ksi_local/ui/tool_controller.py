"""Qt signal bridge to sequential shared tool jobs; no engine on the UI thread."""

import threading
import json
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ksi_local.engine_runner import OperationCancelled
from ksi_local.job_store import JobStatus
from ksi_local.privacy import redact_sensitive_text
from ksi_local.core_service import CoreService
from ksi_local.media_tools import _validate
from ksi_local.tool_jobs import validate_image_request
from ksi_local.ui.strings import text


class ToolController(QObject):
    progress = Signal(float)
    status = Signal(str)
    result = Signal(dict)
    failed = Signal(str)
    finished = Signal()
    busyChanged = Signal(bool)
    activeChanged = Signal(str)
    summary = Signal(object)

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self._busy = False
        self.cancel_event = threading.Event()
        self.thread = None
        self.active_kind = None
        self.finished.connect(self._finished)
        self.activeChanged.connect(self._active_changed)
        self.progress.connect(lambda value: window.progress.setValue(round(value * 100)))
        self.status.connect(window.status.setText)
        self.failed.connect(window.status.setText)
        self.summary.connect(self._summary)

    @property
    def busy(self):
        return self._busy

    def _service(self, roots=()):
        if self.window.workspace is None:
            raise RuntimeError("Önce kullanılabilir bir KSI çalışma alanı gerekir.")
        return CoreService(workspace=self.window.workspace, store=self.window.store, allowed_roots=tuple(roots))

    def _available(self):
        if self.busy or self.window._process_is_running() or self.window.preflight_pending or self.window.maintenance_pending:
            raise RuntimeError("Başka bir işlem sürüyor; kuyruk bitince yeniden deneyin.")

    def start_media(self, requests):
        self._available()
        requests = tuple(requests)
        if not 1 <= len(requests) <= 100:
            raise ValueError("Toplu iş sayısı 1–100 olmalıdır.")
        for request in requests:
            _validate(request)
        roots = [path for request in requests for path in request.sources]
        roots += [request.subtitle for request in requests if request.subtitle]
        service = self._service(roots)
        identifiers = [service.submit_media_tool(asdict(request), confirm=True)["id"] for request in requests]
        self._start(service, identifiers)

    def start_images(self, requests):
        self._available()
        requests = tuple(requests)
        if not 1 <= len(requests) <= 100:
            raise ValueError("Toplu iş sayısı 1–100 olmalıdır.")
        for request in requests:
            validate_image_request(request)
        service = self._service([request["source"] for request in requests])
        identifiers = [service.submit_image_tool(request, confirm=True)["id"] for request in requests]
        self._start(service, identifiers)

    def resume(self, identifier):
        self._available()
        # Resuming is an explicit GUI action granting only this job's input files.
        record = self.window.store.get_job(identifier)
        manifest = Path(record.job_directory) / "tool-request.json"
        if manifest.is_symlink() or manifest.stat().st_size > 2 * 1024**2:
            raise ValueError("Araç iş tanımı güvenli sınırların dışında.")
        request = json.loads(manifest.read_text(encoding="utf-8"))["request"]
        roots = list(request.get("sources", [request.get("source")]))
        if request.get("subtitle"):
            roots.append(request["subtitle"])
        self._start(self._service(roots), [identifier])

    def _start(self, service, identifiers):
        if not identifiers:
            raise ValueError("İşlenecek dosya seçilmedi.")
        self.cancel_event.clear()
        self._busy = True
        self.active_kind = service.store.get_job(identifiers[0]).job_kind.value
        self.busyChanged.emit(True)
        self.window._refresh_history()
        self.thread = threading.Thread(target=self._run, args=(service, identifiers), name="KSI-local-tools", daemon=True)
        self.thread.start()

    def _run(self, service, identifiers):
        counts = {"ok": 0, "failed": 0, "cancelled": 0}
        try:
            for index, identifier in enumerate(identifiers):
                if self.cancel_event.is_set():
                    for pending in identifiers[index:]:
                        if service.store.get_job(pending).status is JobStatus.QUEUED:
                            service.store.transition_job(pending, JobStatus.CANCELLED)
                    counts["cancelled"] += len(identifiers) - index
                    break
                self.status.emit(f"{index + 1}/{len(identifiers)}")
                self.activeChanged.emit(identifier)
                try:
                    result = service.execute_tool_job(identifier, confirm=True, cancel=self.cancel_event, on_progress=lambda value, current=index: self.progress.emit((current + value) / len(identifiers)))
                    self.result.emit(result)
                    counts["ok"] += 1
                except OperationCancelled:
                    counts["cancelled"] += 1
                    continue
                except Exception as error:
                    counts["failed"] += 1
                    self.failed.emit(redact_sensitive_text(str(error))[:1000])
                self.progress.emit((index + 1) / len(identifiers))
        finally:
            try:
                self.summary.emit(counts)
                self.finished.emit()
            except RuntimeError:
                # Only possible when the parent application is shutting down.
                pass

    def _summary(self, counts):
        self.status.emit(text("batch_result", self.window.preferences.ui_language).format(**counts))

    def _finished(self):
        self._busy = False
        self.busyChanged.emit(False)
        if not self.window.closing:
            self.window._refresh_history()
            self.window._schedule_interrupted_job_resume()

    def cancel(self):
        self.cancel_event.set()

    def _active_changed(self, identifier):
        from pathlib import Path

        record = self.window.store.get_job(identifier)
        self.window.current_job_id = identifier
        self.window.current_job = Path(record.job_directory)
        self.window.progress.setRange(0, 100)
        self.window.cancel_button.setEnabled(True)
        self.window._update_job_context(record)
        self.window._refresh_history()
