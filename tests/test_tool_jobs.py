"""Persistent tools jobs use synthetic files and mocked native execution."""

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from ksi_local.job_store import JobKind, JobStatus, JobStore
from ksi_local.media_tools import MediaRequest, MediaResult
from ksi_local.settings import WorkspacePaths
from ksi_local.tool_jobs import ToolJobService, validate_image_request
from ksi_local.engine_runner import OperationCancelled


class ToolJobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = WorkspacePaths(self.root, self.root / "jobs", self.root / "outputs",
                                        self.root / "models/ollama", self.root / "models/whisper",
                                        self.root / "yt-dlp", self.root / "deno")
        self.store = JobStore(self.root / "state/history.sqlite3")
        self.service = ToolJobService(self.workspace, self.store)
        self.source = self.root / "example.mp4"
        self.source.write_bytes(b"synthetic media")
        self.request = MediaRequest((str(self.source),), str(self.root / "outputs/result.mp4"))

    def test_kind_survives_reopening_database(self):
        identifier = self.service.submit_media(self.request)
        self.assertEqual(JobStore(self.store.path).get_job(identifier).job_kind, JobKind.MEDIA)

    def test_invalid_request_fails_before_claim_without_remaining_queued(self):
        identifier = self.service.submit_media(self.request)
        record = self.store.get_job(identifier)
        (Path(record.job_directory) / "tool-request.json").write_text("{invalid")
        with patch("ksi_local.tool_jobs.process_media") as engine:
            with self.assertRaises(ValueError):
                self.service.execute(identifier)
        engine.assert_not_called()
        self.assertEqual(self.store.get_job(identifier).status, JobStatus.FAILED)
        self.assertEqual(self.source.read_bytes(), b"synthetic media")

    def test_cancel_during_cached_output_hash_is_persisted_before_claim(self):
        identifier = self.service.submit_media(self.request)
        def render(request, **options):
            output = Path(request.destination)
            output.write_bytes(b"complete synthetic output")
            return MediaResult(str(output), self.source.stat().st_size, output.stat().st_size, 1, False, ())
        with patch("ksi_local.tool_jobs.process_media", side_effect=render):
            result = self.service.execute(identifier)
        with self.store._connect() as connection:
            connection.execute("UPDATE jobs SET status = ? WHERE id = ?", (JobStatus.QUEUED, identifier))
        event = threading.Event()
        event.set()
        with patch("ksi_local.tool_jobs.process_media") as engine:
            with self.assertRaises(OperationCancelled):
                self.service.execute(identifier, cancel=event)
        engine.assert_not_called()
        self.assertEqual(self.store.get_job(identifier).status, JobStatus.CANCELLED)
        self.assertEqual(Path(result["output"]).read_bytes(), b"complete synthetic output")

    def test_verified_receipt_recovers_published_output_without_rerender(self):
        from ksi_local.exporter import select_job_artifacts

        identifier = self.service.submit_media(self.request)

        def render(request, **options):
            Path(request.destination).write_bytes(b"complete synthetic media")
            return MediaResult(request.destination, self.source.stat().st_size,
                               Path(request.destination).stat().st_size, 1, False, ())

        with patch("ksi_local.tool_jobs.process_media", side_effect=render) as engine:
            result = self.service.execute(identifier)
            self.assertEqual(engine.call_count, 1)
        # Simulate interruption after a verified result, before final job status.
        with self.store._connect() as connection:
            connection.execute("UPDATE jobs SET status = ? WHERE id = ?", (JobStatus.QUEUED, identifier))
        with patch("ksi_local.tool_jobs.process_media") as engine:
            recovered = self.service.execute(identifier)
        engine.assert_not_called()
        self.assertEqual(recovered["output_sha256"], result["output_sha256"])
        self.assertEqual(self.store.get_job(identifier).status, JobStatus.COMPLETED)
        artifacts = select_job_artifacts(self.store.get_job(identifier).job_directory,
                                         export_kind="all", title="Synthetic")
        self.assertEqual([artifact.source for artifact in artifacts], [Path(result["output"])])

    def test_only_one_executor_can_claim_the_job(self):
        identifier = self.service.submit_media(self.request)
        self.store.claim_queued_job(identifier, stage="local_tools")
        with self.assertRaises(ValueError):
            JobStore(self.store.path).claim_queued_job(identifier, stage="local_tools")

    def test_image_validation_rejects_wrong_png_input_before_queueing(self):
        with self.assertRaises(ValueError):
            validate_image_request({"operation": "optimize_png", "source": str(self.source),
                                    "destination": str(self.root / "outputs/result.png")})
        self.assertEqual(self.store.list_jobs(), [])

    def test_result_is_owned_by_job(self):
        identifier = self.service.submit_media(self.request)

        def engine(request, **kwargs):
            path = Path(request.destination)
            path.write_bytes(b"synthetic result")
            return MediaResult(str(path), 15, 16, 10, False, ())

        with patch("ksi_local.tool_jobs.process_media", side_effect=engine):
            result = self.service.execute(identifier)
        record = self.store.get_job(identifier)
        self.assertEqual(record.status, JobStatus.COMPLETED)
        self.assertTrue(Path(result["output"]).is_relative_to(Path(record.job_directory) / "outputs"))
        self.assertFalse(Path(self.request.destination).exists())

    def test_failure_is_retryable_and_recorded(self):
        identifier = self.service.submit_media(self.request)
        with patch("ksi_local.tool_jobs.process_media", side_effect=RuntimeError("synthetic failure")):
            with self.assertRaises(RuntimeError):
                self.service.execute(identifier)
        self.assertEqual(self.store.get_job(identifier).status, JobStatus.FAILED)
        self.assertEqual(self.store.retry_job(identifier).status, JobStatus.QUEUED)

    def test_completed_job_is_not_reprocessed(self):
        identifier = self.service.submit_media(self.request)
        self.store.transition_job(identifier, JobStatus.RUNNING)
        self.store.transition_job(identifier, JobStatus.COMPLETED)
        with self.assertRaises(ValueError):
            self.service.execute(identifier)
