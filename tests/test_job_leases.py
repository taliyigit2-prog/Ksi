"""Live external executors are not mistaken for crashed jobs at GUI startup."""

import tempfile
import unittest
from pathlib import Path

from ksi_local.job_leases import execution_lease, lease_is_active
from ksi_local.job_store import JobStatus, JobStore


class JobLeaseTests(unittest.TestCase):
    def test_live_job_is_not_recovered_until_lease_ends(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = JobStore(root / "state/history.sqlite3")
            store.create_job(job_id="Türkçe iş", source=str(root / "input.mp4"),
                source_language="en", want_subtitle=False, want_summary=False,
                want_dub=False, job_directory=root / "jobs/example", job_kind="media")
            store.claim_queued_job("Türkçe iş", stage="local_tools")
            with execution_lease(store.path.parent, "Türkçe iş"):
                self.assertTrue(lease_is_active(store.path.parent, "Türkçe iş"))
                self.assertEqual(store.recover_interrupted_jobs(workspace_available=True), 0)
                self.assertEqual(store.get_job("Türkçe iş").status, JobStatus.RUNNING)
            self.assertEqual(store.recover_interrupted_jobs(workspace_available=True), 1)
            self.assertEqual(store.get_job("Türkçe iş").status, JobStatus.QUEUED)

    def test_second_executor_cannot_take_live_lease(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            with execution_lease(state, "example"):
                with self.assertRaises(RuntimeError):
                    with execution_lease(state, "example"):
                        pass
            self.assertFalse(lease_is_active(state, "example"))
