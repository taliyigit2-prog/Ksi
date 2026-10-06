"""Persistent local tools jobs using the existing shared SQLite state machine."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import uuid
from dataclasses import asdict, replace
from pathlib import Path
from typing import Callable

from ksi_local.atomic_files import atomic_write_json
from ksi_local.bundle_runtime import OfflinePayload, bundle_root
from ksi_local.engine_runner import OperationCancelled, run_engine
from ksi_local.image_engines import convert_advanced_image, optimize_png
from ksi_local.job_store import JobKind, JobStatus, JobStore, StageStatus
from ksi_local.media_tools import MediaRequest, _validate, process_media
from ksi_local.settings import WorkspacePaths


class ToolJobService:
    def __init__(self, workspace: WorkspacePaths, store: JobStore):
        self.workspace = workspace
        self.store = store

    def _new_job(self, source: str, kind: JobKind, operation: str, request: dict) -> str:
        identifier = uuid.uuid4().hex
        directory = self.workspace.jobs / identifier
        directory.mkdir(parents=True, mode=0o700)
        (directory / "outputs").mkdir(mode=0o700)
        atomic_write_json(directory / "tool-request.json", {"schema_version": 1, "kind": kind.value, "operation": operation, "request": request})
        self.store.create_job(
            job_id=identifier, source=source, source_language="auto", job_kind=kind,
            job_directory=directory, want_subtitle=False, want_summary=False, want_dub=False,
        )
        self.store.ensure_stages(identifier, ["local_tools"])
        return identifier

    def submit_media(self, request: MediaRequest) -> str:
        _validate(request)
        return self._new_job(request.sources[0], JobKind.MEDIA, request.operation, asdict(request))

    def submit_image(self, request: dict) -> str:
        operation = request.get("operation")
        if operation not in {"optimize_png", "convert_image", "remove_background"}:
            raise ValueError("Görsel işlemi desteklenmiyor.")
        path = Path(str(request.get("source", ""))).expanduser()
        if path.is_symlink() or not path.is_file():
            raise ValueError("Görsel girdisi normal bir dosya olmalıdır.")
        allowed_keys = {"operation", "source", "destination", "width", "quality", "strip_metadata"}
        if set(request) - allowed_keys:
            raise ValueError("Görsel istek seçenekleri desteklenmiyor.")
        if Path(str(request.get("destination", ""))).name in {"", ".", ".."}:
            raise ValueError("Görsel çıktı adı geçersiz.")
        return self._new_job(str(path.resolve()), JobKind.IMAGE, operation, request)

    def execute(
        self, identifier: str, *, cancel: threading.Event | None = None,
        on_progress: Callable[[float], None] | None = None,
    ) -> dict:
        record = self.store.get_job(identifier)
        directory = Path(record.job_directory).resolve()
        if record.job_kind not in {JobKind.MEDIA, JobKind.IMAGE} or directory.parent != self.workspace.jobs.resolve():
            raise ValueError("Bu iş yerel araç kuyruğuna ait değil.")
        payload_path = directory / "tool-request.json"
        if payload_path.is_symlink() or payload_path.stat().st_size > 2 * 1024**2:
            raise ValueError("Araç iş tanımı güvenli sınırların dışında.")
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != 1 or payload.get("kind") != record.job_kind.value:
            raise ValueError("Araç iş tanımı geçersiz.")
        request = payload["request"]
        # Results belong to the job and are exportable through the shared client
        # boundary. A persisted request cannot redirect publication elsewhere.
        basename = Path(request["destination"]).name
        if basename in {"", ".", ".."} or any(ch in basename for ch in ("\x00", "\n", "\r")):
            raise ValueError("Araç çıktı dosyası adı geçersiz.")
        output = directory / "outputs" / basename
        if output.parent.is_symlink() or output.exists() or output.is_symlink():
            raise FileExistsError("Mevcut araç çıktısına yazılmaz.")
        if record.status is not JobStatus.QUEUED:
            self.store.retry_job(identifier)
        self.store.transition_job(identifier, JobStatus.RUNNING, current_stage="local_tools")
        self.store.set_stage(identifier, "local_tools", StageStatus.RUNNING)
        try:
            if record.job_kind is JobKind.MEDIA:
                media = MediaRequest(**dict(request, sources=tuple(request["sources"])))
                result = process_media(replace(media, destination=str(output)), cancel=cancel, on_progress=on_progress).to_dict()
            elif payload["operation"] == "optimize_png":
                result = asdict(optimize_png(request["source"], output, strip_metadata=bool(request.get("strip_metadata")), cancel=cancel))
            elif payload["operation"] == "convert_image":
                result = asdict(convert_advanced_image(request["source"], output, width=request.get("width"), quality=request.get("quality", 85), strip_metadata=bool(request.get("strip_metadata")), cancel=cancel))
            else:
                result = self._background(request["source"], output, cancel=cancel)
            atomic_write_json(directory / "tool-result.json", result)
            self.store.set_stage(identifier, "local_tools", StageStatus.COMPLETED)
            self.store.transition_job(identifier, JobStatus.COMPLETED)
            return result
        except Exception as error:
            cancelled = isinstance(error, OperationCancelled)
            self.store.set_stage(identifier, "local_tools", StageStatus.CANCELLED if cancelled else StageStatus.FAILED, error=str(error))
            self.store.transition_job(identifier, JobStatus.CANCELLED if cancelled else JobStatus.FAILED, error=str(error))
            raise

    def _background(self, source: str, output: Path, *, cancel: threading.Event | None) -> dict:
        root = bundle_root()
        if root is None:
            raise RuntimeError("AI arka plan modeli doğrulanmış çevrimdışı pakette bulunmalıdır.")
        payload = OfflinePayload.load(root)
        model = payload.component("model", "u2netp")
        expected = next(entry.sha256 for entry in payload.files if entry.identifier == "u2netp" and entry.role == "model")
        with tempfile.TemporaryDirectory(prefix=".ksi-ai-", dir=output.parent) as temporary:
            stage = Path(temporary)
            candidate = stage / "result.png"
            job = stage / "request.json"
            atomic_write_json(job, {"operation": "remove_background", "source": source, "destination": str(candidate), "model": str(model), "model_sha256": expected})
            result_file = stage / "result.json"
            run_engine([sys.executable, "-m", "ksi_local.local_ai_worker", str(job), str(result_file)], timeout=600, cancel=cancel)
            if result_file.stat().st_size > 65536:
                raise RuntimeError("AI işçi yanıtı boyut sınırını aşıyor.")
            result = json.loads(result_file.read_text(encoding="utf-8"))
            if not result.get("ok") or not candidate.is_file():
                raise RuntimeError("AI arka plan işlemi doğrulanamadı.")
            if cancel is not None and cancel.is_set():
                raise OperationCancelled("AI arka plan işlemi iptal edildi.")
            os.link(candidate, output)
            result["output"] = str(output)
            return result
