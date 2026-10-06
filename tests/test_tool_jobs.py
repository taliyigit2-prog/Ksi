"""Persistent tools jobs use synthetic files and mocked native execution."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ksi_local.job_store import JobKind, JobStatus, JobStore
from ksi_local.media_tools import MediaRequest, MediaResult
from ksi_local.settings import WorkspacePaths
from ksi_local.tool_jobs import ToolJobService, validate_image_request


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
