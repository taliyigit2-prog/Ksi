"""Persistent local tools jobs using the existing shared SQLite state machine."""

from __future__ import annotations

import json
import hashlib
import os
import sys
import tempfile
import threading
import uuid
from dataclasses import asdict, replace
from pathlib import Path
from typing import Callable

from ksi_local.atomic_files import atomic_write_json
from ksi_local.bundle_runtime import OfflinePayload, bundle_root, digest_file
from ksi_local.engine_runner import OperationCancelled, run_engine
from ksi_local.image_engines import convert_advanced_image, optimize_png
from ksi_local.job_store import JobKind, JobStatus, JobStore, StageStatus
from ksi_local.media_tools import MediaRequest, _validate, process_media
from ksi_local.settings import WorkspacePaths
from ksi_local.job_leases import execution_lease


class JobCancellation:
    def __init__(self, store: JobStore, identifier: str, event: threading.Event | None):
        self.store, self.identifier, self.event = store, identifier, event

    def is_set(self):
        return bool((self.event is not None and self.event.is_set()) or self.store.get_job(self.identifier).status in {JobStatus.CANCELLED, JobStatus.PAUSED, JobStatus.WAITING_FOR_SSD})


def validate_image_request(request: dict) -> None:
    if not isinstance(request, dict):
        raise ValueError("Görsel istek tanımı geçersiz.")
    operation = request.get("operation")
    if operation not in {"optimize_png", "convert_image", "remove_background"}:
        raise ValueError("Görsel işlemi desteklenmiyor.")
    allowed_keys = {"operation", "source", "destination", "width", "quality", "strip_metadata"}
    if set(request) - allowed_keys:
        raise ValueError("Görsel istek seçenekleri desteklenmiyor.")
    if not all(isinstance(request.get(key), str) and request[key] for key in ("source", "destination")):
        raise ValueError("Görsel dosya yolları geçersiz.")
    path = Path(request["source"]).expanduser()
    output = Path(request["destination"]).expanduser()
    if path.is_symlink() or not path.is_file():
        raise ValueError("Görsel girdisi normal bir dosya olmalıdır.")
    if not output.is_absolute() or output.is_symlink() or output.exists() or output.resolve() == path.resolve():
        raise ValueError("Görsel çıktısı yeni ve mutlak bir dosya yolu olmalıdır.")
    if any(char in str(output) for char in ("\x00", "\r", "\n")):
        raise ValueError("Görsel çıktı adında denetim karakteri olamaz.")
    allowed = {".png", ".jpg", ".jpeg", ".webp", ".heic", ".avif", ".gif", ".tif", ".tiff"}
    if path.suffix.lower() not in allowed or output.suffix.lower() not in allowed:
        raise ValueError("Görsel biçimi desteklenmiyor.")
    if operation in {"optimize_png", "remove_background"} and output.suffix.lower() != ".png":
        raise ValueError("Bu görsel işlemi PNG çıktı gerektirir.")
    if operation == "optimize_png" and path.suffix.lower() != ".png":
        raise ValueError("PNG optimizasyonu PNG girdi gerektirir.")
    quality, width = request.get("quality", 85), request.get("width")
    if type(quality) is not int or not 1 <= quality <= 100:
        raise ValueError("Kalite 1–100 arasında tam sayı olmalıdır.")
    if width is not None and (type(width) is not int or not 1 <= width <= 6000):
        raise ValueError("Genişlik 1–6000 arasında tam sayı olmalıdır.")
    if type(request.get("strip_metadata", False)) is not bool:
        raise ValueError("Metadata seçimi geçersiz.")


class ToolJobService:
    def __init__(self, workspace: WorkspacePaths, store: JobStore):
        from ksi_local.internal_storage import validate_internal_path

        validate_internal_path(workspace.root)
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
        validate_image_request(request)
        normalized = dict(request, source=str(Path(request["source"]).expanduser().resolve()))
        return self._new_job(normalized["source"], JobKind.IMAGE, request["operation"], normalized)

    def execute(
        self, identifier: str, *, cancel: threading.Event | None = None,
        on_progress: Callable[[float], None] | None = None,
    ) -> dict:
        with execution_lease(self.store.path.parent, identifier):
            try:
                return self._execute_owned(identifier, cancel=cancel, on_progress=on_progress)
            except OperationCancelled as error:
                # Receipt hashing happens before the SQL claim. An explicit
                # cancellation there must not leave a supposedly queued job
                # eligible for another automatic execution. Completed outputs
                # and previously completed stages remain intact for resume.
                record = self.store.get_job(identifier)
                if record.job_kind in {JobKind.MEDIA, JobKind.IMAGE} and record.status in {JobStatus.QUEUED, JobStatus.RUNNING}:
                    self.store.ensure_stages(identifier, ["local_tools"])
                    stage = self.store.get_stage(identifier, "local_tools")
                    if stage.status in {StageStatus.PENDING, StageStatus.RUNNING}:
                        self.store.set_stage(identifier, "local_tools", StageStatus.CANCELLED, error=str(error))
                    self.store.transition_job(identifier, JobStatus.CANCELLED, error=str(error))
                raise

            except (OSError, ValueError, RuntimeError, KeyError) as error:
                # Validation can fail before claiming QUEUED -> RUNNING. Do
                # not leave a broken request eligible for automatic retry.
                # A job belonging to a different workspace is not ours to edit.
                record = self.store.get_job(identifier)
                if (record.job_kind in {JobKind.MEDIA, JobKind.IMAGE}
                        and record.status is JobStatus.QUEUED
                        and Path(record.job_directory).resolve().parent == self.workspace.jobs.resolve()):
                    self.store.transition_job(identifier, JobStatus.FAILED, error=str(error))
                raise

    def _execute_owned(
        self, identifier: str, *, cancel: threading.Event | None = None,
        on_progress: Callable[[float], None] | None = None,
    ) -> dict:
        record = self.store.get_job(identifier)
        if record.status is JobStatus.COMPLETED:
            raise ValueError("Tamamlanmış araç işi yeniden yürütülemez.")
        directory = Path(record.job_directory).resolve()
        if record.job_kind not in {JobKind.MEDIA, JobKind.IMAGE} or directory.parent != self.workspace.jobs.resolve():
            raise ValueError("Bu iş yerel araç kuyruğuna ait değil.")
        payload_path = directory / "tool-request.json"
        if payload_path.is_symlink() or payload_path.stat().st_size > 2 * 1024**2:
            raise ValueError("Araç iş tanımı güvenli sınırların dışında.")
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") != 1 or payload.get("kind") != record.job_kind.value:
            raise ValueError("Araç iş tanımı geçersiz.")
        request = payload["request"]
        if not isinstance(request, dict) or payload.get("operation") != request.get("operation"):
            raise ValueError("Araç işlem tanımı tutarsız.")
        # Results belong to the job and are exportable through the shared client
        # boundary. A persisted request cannot redirect publication elsewhere.
        destination = request.get("destination")
        if not isinstance(destination, str):
            raise ValueError("Araç çıktı yolu geçersiz.")
        basename = Path(destination).name
        if basename in {"", ".", ".."} or any(ch in basename for ch in ("\x00", "\n", "\r")):
            raise ValueError("Araç çıktı dosyası adı geçersiz.")
        output = directory / "outputs" / basename
        if output.parent.is_symlink() or output.is_symlink():
            raise FileExistsError("Mevcut araç çıktısına yazılmaz.")
        fingerprint = hashlib.sha256(json.dumps(request, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        sources = request.get("sources", [request.get("source")])
        if not isinstance(sources, list) or not sources or len(sources) > 100 or not all(isinstance(source, str) and source for source in sources):
            raise ValueError("Araç kaynak yolları geçersiz.")
        sources = list(sources)
        if request.get("subtitle"):
            if not isinstance(request["subtitle"], str):
                raise ValueError("Altyazı yolu geçersiz.")
            sources.append(request["subtitle"])
        stamps = []
        for source in sources:
            path = Path(source)
            if path.is_file() and not path.is_symlink():
                info = path.stat()
                stamps.append([info.st_size, info.st_mtime_ns, info.st_dev, info.st_ino])
            else:
                stamps.append(None)
        reusable = None
        receipt = directory / "tool-result.json"
        if receipt.is_file() and not receipt.is_symlink() and receipt.stat().st_size <= 65536:
            try:
                data = json.loads(receipt.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("Araç sonuç kaydı geçersiz.")
                previous = Path(data["output"])
                old_stamps = data.get("source_stamps", [])
                if previous.parent == output.parent and previous.is_file() and not previous.is_symlink() and data.get("request_sha256") == fingerprint and len(old_stamps) == len(stamps) and all(now is None or now == old for old, now in zip(old_stamps, stamps)) and digest_file(previous, cancel=cancel) == data.get("output_sha256"):
                    reusable = data
            except (KeyError, TypeError, ValueError, OSError):
                pass
        if reusable is None and output.exists():
            # An interrupted publish without a valid receipt is not overwritten.
            output = output.parent / ("result-retry-" + uuid.uuid4().hex[:12] + output.suffix)
        if reusable is None and record.job_kind is JobKind.IMAGE:
            validate_image_request(dict(request, destination=str(output)))
        elif reusable is None:
            _validate(MediaRequest(**dict(request, sources=tuple(request["sources"]), destination=str(output))))
        if record.status is not JobStatus.QUEUED:
            self.store.retry_job(identifier)
        self.store.ensure_stages(identifier, ["local_tools"])
        stage = self.store.get_stage(identifier, "local_tools")
        if stage.status is StageStatus.COMPLETED and reusable is None:
            self.store.reset_completed_stage(identifier, "local_tools")
        self.store.claim_queued_job(identifier, stage="local_tools")
        if stage.status is not StageStatus.COMPLETED or reusable is None:
            self.store.set_stage(identifier, "local_tools", StageStatus.RUNNING)
        cancel = JobCancellation(self.store, identifier, cancel)
        try:
            if reusable is not None:
                result = reusable
            elif record.job_kind is JobKind.MEDIA:
                media = MediaRequest(**dict(request, sources=tuple(request["sources"])))
                result = process_media(replace(media, destination=str(output)), cancel=cancel, on_progress=on_progress).to_dict()
            elif payload["operation"] == "optimize_png":
                result = asdict(optimize_png(request["source"], output, strip_metadata=bool(request.get("strip_metadata")), cancel=cancel))
            elif payload["operation"] == "convert_image":
                result = asdict(convert_advanced_image(request["source"], output, width=request.get("width"), quality=request.get("quality", 85), strip_metadata=bool(request.get("strip_metadata")), cancel=cancel))
            else:
                result = self._background(request["source"], output, cancel=cancel)
            if reusable is None:
                for source, stamp in zip(sources, stamps):
                    info = Path(source).stat()
                    if [info.st_size, info.st_mtime_ns, info.st_dev, info.st_ino] != stamp:
                        raise RuntimeError("İşlem sırasında kaynak değişti; çıktı doğrulanmış olarak işaretlenmedi.")
                result.update(request_sha256=fingerprint, source_stamps=stamps,
                              output_sha256=digest_file(output, cancel=cancel))
            atomic_write_json(directory / "tool-result.json", result)
            if cancel.is_set():
                raise OperationCancelled("İş iptal edildi; üretilmiş dosyalar korundu.")
            if self.store.get_stage(identifier, "local_tools").status is not StageStatus.COMPLETED:
                self.store.set_stage(identifier, "local_tools", StageStatus.COMPLETED)
            self.store.transition_job(identifier, JobStatus.COMPLETED)
            return result
        except Exception as error:
            cancelled = isinstance(error, OperationCancelled)
            stage = self.store.get_stage(identifier, "local_tools")
            if stage.status in {StageStatus.PENDING, StageStatus.RUNNING}:
                self.store.set_stage(identifier, "local_tools", StageStatus.CANCELLED if cancelled else StageStatus.FAILED, error=str(error))
            # A second client may already have paused/cancelled the persisted job.
            if self.store.get_job(identifier).status is JobStatus.RUNNING:
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
