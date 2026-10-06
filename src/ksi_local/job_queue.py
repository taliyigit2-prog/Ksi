"""Small queue coordinator kept independent from Qt for deterministic tests."""

from __future__ import annotations

from collections.abc import Callable

from ksi_local.job_store import JobRecord, JobStatus, JobStore, StageStatus
from ksi_local.worker_protocol import WorkerEvent


class JobQueue:
    def __init__(self, store: JobStore, workspace_available: Callable[[], bool]) -> None:
        self.store = store
        self.workspace_available = workspace_available

    def recover(self) -> int:
        return self.store.recover_interrupted_jobs(
            workspace_available=self.workspace_available()
        )

    def claim_next(self) -> JobRecord | None:
        return self.store.claim_next_job(workspace_available=self.workspace_available())

    def handle_event(self, job_id: str, event: WorkerEvent) -> None:
        if event.event == "started":
            self.store.set_stage(job_id, event.stage, StageStatus.RUNNING)
        elif event.event == "progress":
            assert event.completed is not None and event.total is not None
            self.store.update_stage_progress(job_id, event.stage, event.completed, event.total)
        elif event.event == "completed":
            self.store.set_stage(job_id, event.stage, StageStatus.COMPLETED)
        elif event.event == "error":
            message = event.message or "İşçi bilinmeyen bir hata bildirdi."
            self.store.set_stage(job_id, event.stage, StageStatus.FAILED, error=message)
            self.store.transition_job(job_id, JobStatus.FAILED, error=message)
        elif event.message:
            self.store.append_log(job_id, event.message)

    def complete(self, job_id: str) -> JobRecord:
        return self.store.transition_job(job_id, JobStatus.COMPLETED)

    def cancel(self, job_id: str) -> JobRecord:
        job = self.store.get_job(job_id)
        if job.current_stage:
            self.store.set_stage(job_id, job.current_stage, StageStatus.CANCELLED)
        if job.status == JobStatus.COMPLETED:
            raise ValueError("Tamamlanmış iş iptal edilemez.")
        if job.status == JobStatus.CANCELLED:
            return job
        return self.store.transition_job(job_id, JobStatus.CANCELLED)
