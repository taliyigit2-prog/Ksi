"""Client-neutral service boundary shared by GUI, CLI and MCP."""

from __future__ import annotations

import uuid
from dataclasses import asdict
from functools import wraps
from pathlib import Path
from typing import Any

from ksi_local.exporter import (
    ALLOWED_ARTIFACT_SUFFIXES,
    export_artifacts,
    select_job_artifacts,
)
from ksi_local.job_queue import JobQueue
from ksi_local.job_store import JobRecord, JobStatus, JobStore
from ksi_local.preflight import inspect_source
from ksi_local.privacy import redact_sensitive_text
from ksi_local.settings import WorkspacePaths
from ksi_local.media_tools import MediaRequest
from ksi_local.tool_jobs import ToolJobService
from ksi_local.workspace_access import workspace_reader


def _workspace_operation(function):
    @workspace_reader
    @wraps(function)
    def wrapped(self, *args, **kwargs):
        self._validate_workspace()
        return function(self, *args, **kwargs)
    return wrapped


def _record(job: JobRecord) -> dict[str, Any]:
    payload = asdict(job)
    payload["job_kind"] = job.job_kind.value
    payload["status"] = job.status.value
    payload["last_error"] = redact_sensitive_text(job.last_error) if job.last_error else None
    return payload


class CoreService:
    def __init__(
        self,
        *,
        store: JobStore | None = None,
        workspace: WorkspacePaths | None = None,
        allowed_roots: tuple[str | Path, ...] = (),
        network_allowed: bool = False,
    ) -> None:
        self.store = store or JobStore()
        self.workspace = workspace
        roots = [Path(item).expanduser().resolve() for item in allowed_roots]
        if workspace is not None:
            roots.append(workspace.root.resolve())
        self.allowed_roots = tuple(dict.fromkeys(roots))
        self.network_allowed = network_allowed

    def set_workspace(self, workspace: WorkspacePaths | None) -> None:
        """Refresh the internal workspace without changing explicit client roots."""
        previous = self.workspace.root.resolve() if self.workspace is not None else None
        roots = [root for root in self.allowed_roots if root != previous]
        self.workspace = workspace
        if workspace is not None and workspace.root.resolve() not in roots:
            roots.append(workspace.root.resolve())
        self.allowed_roots = tuple(roots)

    def _validate_workspace(self) -> None:
        """An idle client must not resume writes against an obsolete selection."""
        if self.workspace is None:
            return
        from ksi_local.internal_storage import validate_internal_path

        validate_internal_path(self.workspace.root)
        from ksi_local.workspace_management import load_selection
        from ksi_local.settings import resolve_workspace
        selection = load_selection()
        if selection is None:
            return  # Explicit legacy/test workspaces retain their existing policy.
        if Path(selection.workspace_root).expanduser().resolve() != self.workspace.root.resolve():
            raise RuntimeError("Çalışma alanı başka bir istemcide değiştirildi; konumu yenileyin.")
        current = resolve_workspace(initialize=False)
        if current.root.resolve() != self.workspace.root.resolve():
            raise RuntimeError("Aktif çalışma alanı doğrulanamadı.")

    def _allowed_path(self, value: str | Path, *, must_exist: bool = True) -> Path:
        raw = Path(value).expanduser()
        if raw.is_symlink():
            raise PermissionError("Sembolik bağlantı MCP/çekirdek dosya kökü sınırını aşamaz.")
        path = raw.resolve()
        if must_exist and not path.exists():
            raise FileNotFoundError("İzin verilen kökte dosya bulunamadı.")
        if not any(path == root or root in path.parents for root in self.allowed_roots):
            raise PermissionError("Dosya izin verilen KSI köklerinin dışında.")
        return path

    @_workspace_operation
    def preflight(self, source: str, **options: Any) -> dict[str, Any]:
        if self.workspace is None:
            raise RuntimeError("Ön inceleme için bağlı KSI-Workspace gereklidir.")
        normalized = source.strip()
        if normalized.casefold().startswith("https://"):
            if not self.network_allowed:
                raise PermissionError("Ağ kaynağı için açık ağ yetkisi gereklidir.")
        else:
            normalized = str(self._allowed_path(normalized))
        result = inspect_source(normalized, workspace=self.workspace, **options)
        return result.to_dict()

    @_workspace_operation
    def create_job(
        self,
        *,
        source: str,
        job_directory: str | Path,
        source_language: str = "auto",
        want_subtitle: bool = False,
        want_summary: bool = False,
        want_dub: bool = False,
        download_only: bool = False,
        job_kind: str = "video",
        job_id: str | None = None,
    ) -> dict[str, Any]:
        normalized = source.strip()
        if normalized.casefold().startswith("https://"):
            if not self.network_allowed:
                raise PermissionError("Ağ işi için açık ağ yetkisi gereklidir.")
        else:
            normalized = str(self._allowed_path(normalized))
        directory = self._allowed_path(job_directory, must_exist=False)
        if directory.exists() and (directory.is_symlink() or any(directory.iterdir())):
            raise FileExistsError("İş klasörü yeni ve boş olmalıdır.")
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        identifier = job_id or str(uuid.uuid4())
        return _record(
            self.store.create_job(
                job_id=identifier,
                source=normalized,
                source_language=source_language,
                want_subtitle=want_subtitle,
                want_summary=want_summary,
                want_dub=want_dub,
                download_only=download_only,
                job_kind=job_kind,
                job_directory=directory,
            )
        )

    @_workspace_operation
    def status(self, job_id: str) -> dict[str, Any]:
        job = self.store.get_job(job_id)
        self._allowed_path(job.job_directory, must_exist=False)
        payload = _record(job)
        payload["stages"] = [asdict(item) for item in self.store.list_stages(job_id)]
        return payload

    @_workspace_operation
    def submit_media_tool(self, request: dict[str, Any], *, confirm: bool) -> dict[str, Any]:
        if confirm is not True or self.workspace is None:
            raise PermissionError("Yerel araç işi için açık onay ve çalışma alanı gerekir.")
        sources = request.get("sources")
        if not isinstance(sources, (tuple, list)) or not 1 <= len(sources) <= 100 or any(not isinstance(source, str) or not 0 < len(source) <= 4096 for source in sources):
            raise ValueError("Medya girdisi listesi geçersiz.")
        paths = tuple(str(self._allowed_path(source)) for source in sources)
        output = self._allowed_path(request["destination"], must_exist=False)
        data = dict(request, sources=paths, destination=str(output))
        if data.get("subtitle"):
            data["subtitle"] = str(self._allowed_path(data["subtitle"]))
        identifier = ToolJobService(self.workspace, self.store).submit_media(MediaRequest(**data))
        return self.status(identifier)

    @_workspace_operation
    def execute_tool_job(self, job_id: str, *, confirm: bool, cancel=None, on_progress=None) -> dict[str, Any]:
        if confirm is not True or self.workspace is None:
            raise PermissionError("Yerel araç işlemi için açık onay ve çalışma alanı gerekir.")
        import json

        record = self.store.get_job(job_id)
        directory = self._allowed_path(record.job_directory)
        manifest = self._allowed_path(directory / "tool-request.json")
        if manifest.stat().st_size > 2 * 1024**2:
            raise ValueError("Araç iş tanımı boyut sınırını aşıyor.")
        data = json.loads(manifest.read_text(encoding="utf-8"))["request"]
        for source in data.get("sources", [data.get("source")]):
            self._allowed_path(source, must_exist=False)
        if data.get("subtitle"):
            self._allowed_path(data["subtitle"], must_exist=False)
        return ToolJobService(self.workspace, self.store).execute(job_id, cancel=cancel, on_progress=on_progress)

    @_workspace_operation
    def submit_image_tool(self, request: dict[str, Any], *, confirm: bool) -> dict[str, Any]:
        if confirm is not True or self.workspace is None:
            raise PermissionError("Yerel görsel işi için açık onay ve çalışma alanı gerekir.")
        source = self._allowed_path(request["source"])
        destination = self._allowed_path(request["destination"], must_exist=False)
        data = dict(request, source=str(source), destination=str(destination))
        identifier = ToolJobService(self.workspace, self.store).submit_image(data)
        return self.status(identifier)

    @_workspace_operation
    def stop(self, job_id: str, *, confirm: bool) -> dict[str, Any]:
        if confirm is not True:
            raise PermissionError("İşi durdurmak için açık onay gereklidir.")
        self._allowed_path(self.store.get_job(job_id).job_directory, must_exist=False)
        queue = JobQueue(self.store, lambda: self.workspace is not None)
        return _record(queue.cancel(job_id))

    @_workspace_operation
    def resume(self, job_id: str) -> dict[str, Any]:
        self._allowed_path(self.store.get_job(job_id).job_directory, must_exist=False)
        return _record(self.store.retry_job(job_id))

    @_workspace_operation
    def results(self, job_id: str) -> list[dict[str, Any]]:
        job = self.store.get_job(job_id)
        root = self._allowed_path(job.job_directory)
        output_root = root / "outputs"
        if not output_root.is_dir() or output_root.is_symlink():
            return []
        items: list[dict[str, Any]] = []
        for path in sorted(output_root.rglob("*")):
            if len(items) >= 500:
                break
            if (
                path.is_file()
                and not path.is_symlink()
                and path.suffix.casefold() in ALLOWED_ARTIFACT_SUFFIXES
                and not any(part.startswith(".") for part in path.relative_to(output_root).parts)
            ):
                items.append({"path": str(path), "relative_path": str(path.relative_to(root)), "size_bytes": path.stat().st_size})
        return items

    @_workspace_operation
    def export(
        self,
        job_id: str,
        *,
        destination_directory: str | Path,
        folder_name: str = "KSI Local Studio Çıktısı",
        confirm: bool,
    ) -> dict[str, Any]:
        if confirm is not True:
            raise PermissionError("Dışa aktarma yazma işlemi için açık onay gereklidir.")
        destination = self._allowed_path(destination_directory)
        job = self.store.get_job(job_id)
        self._allowed_path(job.job_directory)
        artifacts = select_job_artifacts(
            job.job_directory, export_kind="all", title=f"KSI Local Studio {job.id[:8]}"
        )
        output = export_artifacts(artifacts, desktop=destination, folder_name=folder_name)
        return {"output_directory": str(output), "source_preserved": True}
