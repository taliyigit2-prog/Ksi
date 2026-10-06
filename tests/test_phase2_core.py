from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from ksi_local.atomic_files import atomic_replace, atomic_write_json, atomic_write_text
from ksi_local.job_queue import JobQueue
from ksi_local.job_store import JobStatus, JobStore, StageStatus
from ksi_local.privacy import redact_sensitive_text, safe_source_reference
from ksi_local.worker_protocol import (
    JSONLBuffer,
    WorkerEvent,
    encode_worker_event,
    parse_worker_event,
)


class PrivacyTests(unittest.TestCase):
    def test_redacts_secrets_and_url_query_from_logs(self) -> None:
        text = (
            "Authorization: Bearer abc.def Cookie=session-secret "
            "https://example.com/watch?v=public&token=private"
        )
        redacted = redact_sensitive_text(text)
        self.assertNotIn("abc.def", redacted)
        self.assertNotIn("session-secret", redacted)
        self.assertNotIn("private", redacted)
        self.assertIn("[gizlendi]", redacted)

    def test_source_reference_keeps_youtube_id_but_drops_other_query(self) -> None:
        kind, source = safe_source_reference(
            "https://www.youtube.com/watch?v=video123&token=secret&t=30#part"
        )
        self.assertEqual(kind, "youtube")
        self.assertEqual(source, "https://www.youtube.com/watch?v=video123")


class AtomicFileTests(unittest.TestCase):
    def test_atomic_writes_replace_content_without_leaving_parts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "checkpoint.json"
            atomic_write_text(target, "old")
            atomic_write_json(target, {"value": "new"})
            self.assertEqual(json.loads(target.read_text()), {"value": "new"})
            self.assertFalse(list(Path(directory).glob("*.part")))
            self.assertEqual(os.stat(target).st_mode & 0o777, 0o600)

    def test_atomic_replace_promotes_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkpoint = root / "output.srt.partial"
            target = root / "output.srt"
            atomic_write_text(checkpoint, "completed")
            atomic_replace(checkpoint, target)
            self.assertEqual(target.read_text(), "completed")
            self.assertFalse(checkpoint.exists())


class WorkerProtocolTests(unittest.TestCase):
    def test_round_trip_and_split_chunks(self) -> None:
        first = encode_worker_event(WorkerEvent("started", "fake"))
        second = encode_worker_event(WorkerEvent("progress", "fake", 2, 5))
        buffer = JSONLBuffer()
        self.assertEqual(buffer.feed(first[:8]), [])
        lines = buffer.feed(first[8:] + "\n" + second + "\n")
        self.assertEqual(
            [parse_worker_event(line).event for line in lines],
            ["started", "progress"],
        )
        self.assertEqual(parse_worker_event(lines[1]).completed, 2)

    def test_protocol_rejects_invalid_progress(self) -> None:
        with self.assertRaises(ValueError):
            parse_worker_event('{"event":"progress","stage":"x","completed":6,"total":5}')

    def test_protocol_redacts_log_message(self) -> None:
        line = encode_worker_event(
            WorkerEvent("log", "fake", message="password=hunter2 Authorization: Bearer abc")
        )
        self.assertNotIn("hunter2", line)
        self.assertNotIn("Bearer abc", line)


class JobStoreFixture:
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = JobStore(self.root / "state" / "jobs.sqlite3")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def create_job(self, job_id: str = "job-1"):
        return self.store.create_job(
            job_id=job_id,
            source="https://www.youtube.com/watch?v=abc&token=secret",
            source_language="auto",
            want_subtitle=True,
            want_summary=True,
            want_dub=False,
            job_directory=self.root / job_id,
        )


class JobStoreTests(JobStoreFixture, unittest.TestCase):

    def test_database_reopens_with_job_and_private_permissions(self) -> None:
        self.create_job()
        reopened = JobStore(self.store.path)
        record = reopened.get_job("job-1")
        self.assertEqual(record.status, JobStatus.QUEUED)
        self.assertNotIn("secret", record.source_reference)
        self.assertEqual(os.stat(self.store.path).st_mode & 0o777, 0o600)

    def test_job_and_stage_state_machine(self) -> None:
        self.create_job()
        self.store.ensure_stages("job-1", ["download", "transcribe"])
        self.store.transition_job("job-1", JobStatus.RUNNING, current_stage="download")
        self.store.set_stage("job-1", "download", StageStatus.RUNNING)
        self.store.update_stage_progress("job-1", "download", 3, 10)
        self.store.set_stage("job-1", "download", StageStatus.COMPLETED)
        stage = self.store.get_stage("job-1", "download")
        assert stage is not None
        self.assertEqual(stage.status, StageStatus.COMPLETED)
        self.assertEqual((stage.completed, stage.total), (3, 10))
        with self.assertRaises(ValueError):
            self.store.set_stage("job-1", "download", StageStatus.RUNNING)

    def test_interrupted_job_recovers_or_waits_for_ssd(self) -> None:
        self.create_job()
        self.store.ensure_stages("job-1", ["fake"])
        self.store.transition_job("job-1", JobStatus.RUNNING, current_stage="fake")
        self.store.set_stage("job-1", "fake", StageStatus.RUNNING)
        self.assertEqual(self.store.recover_interrupted_jobs(workspace_available=False), 1)
        self.assertEqual(self.store.get_job("job-1").status, JobStatus.WAITING_FOR_SSD)
        stage = self.store.get_stage("job-1", "fake")
        assert stage is not None
        self.assertEqual(stage.status, StageStatus.PENDING)
        self.assertEqual(self.store.resume_waiting_jobs(), 1)
        self.assertEqual(self.store.get_job("job-1").status, JobStatus.QUEUED)

    def test_persisted_logs_are_sanitized(self) -> None:
        self.create_job()
        self.store.append_log(
            "job-1", "Cookie: session-secret https://example.com/?token=value"
        )
        saved = "\n".join(self.store.read_logs("job-1"))
        self.assertNotIn("session-secret", saved)
        self.assertNotIn("token=value", saved)


class QueueAcceptanceTests(JobStoreFixture, unittest.TestCase):
    def test_fake_worker_queue_cancel_reopen_and_ssd_wait(self) -> None:
        self.create_job()
        self.store.ensure_stages("job-1", ["fake"])
        available = False
        queue = JobQueue(self.store, lambda: available)
        self.assertIsNone(queue.claim_next())
        self.assertEqual(self.store.get_job("job-1").status, JobStatus.WAITING_FOR_SSD)

        available = True
        self.store.resume_waiting_jobs()
        claimed = queue.claim_next()
        assert claimed is not None
        self.assertEqual(claimed.status, JobStatus.RUNNING)
        queue.handle_event("job-1", WorkerEvent("started", "fake"))
        queue.handle_event("job-1", WorkerEvent("progress", "fake", 1, 2))
        queue.cancel("job-1")
        self.assertEqual(JobStore(self.store.path).get_job("job-1").status, JobStatus.CANCELLED)

        self.store.retry_job("job-1")
        claimed = JobQueue(JobStore(self.store.path), lambda: True).claim_next()
        assert claimed is not None
        queue.handle_event("job-1", WorkerEvent("started", "fake"))
        queue.handle_event("job-1", WorkerEvent("completed", "fake"))
        queue.complete("job-1")
        self.assertEqual(JobStore(self.store.path).get_job("job-1").status, JobStatus.COMPLETED)


if __name__ == "__main__":
    unittest.main()
