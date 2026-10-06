from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ksi_local.gui import MainWindow
from ksi_local.job_store import JobStatus, StageStatus
from ksi_local.settings import WorkspacePaths


class GuiPhase2AcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.environment = patch.dict(
            os.environ,
            {"KSI_STATE_DIRECTORY": str(self.root / "state")},
        )
        self.environment.start()
        workspace_root = self.root / "KSI-Workspace"
        self.workspace = WorkspacePaths(
            root=workspace_root,
            jobs=workspace_root / "jobs",
            outputs=workspace_root / "outputs",
            models_ollama=workspace_root / "models/ollama",
            models_whisper=workspace_root / "models/whisper",
            yt_dlp=workspace_root / "tools/yt-dlp",
            deno=workspace_root / "tools/deno",
        )
        self.workspace.jobs.mkdir(parents=True)
        self.resolver = patch("ksi_local.gui.resolve_workspace", return_value=self.workspace)
        self.resolve_workspace = self.resolver.start()
        self.window = MainWindow()
        self.window.workspace_initialized = True
        self.window._workspace_resolution_finished(self.workspace, None, True)

    def tearDown(self) -> None:
        if self.window._process_is_running():
            self.window._cancel()
            self._wait_until(lambda: not self.window._process_is_running())
        self.window.close()
        self.resolver.stop()
        self.environment.stop()
        self.temporary.cleanup()

    def _wait_until(self, predicate, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.application.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Qt olayı zamanında tamamlanmadı.")

    def _create_job(self, job_id: str) -> Path:
        job_directory = self.workspace.jobs / job_id
        (job_directory / "source").mkdir(parents=True)
        (job_directory / "outputs").mkdir()
        source = self.root / f"{job_id}.srt"
        source.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
        self.window.store.create_job(
            job_id=job_id,
            source=str(source),
            source_language="en",
            want_subtitle=True,
            want_summary=False,
            want_dub=False,
            job_directory=job_directory,
        )
        self.window.current_job_id = job_id
        self.window.current_job = job_directory
        return job_directory

    def test_fake_worker_runs_through_jsonl_queue(self) -> None:
        self._create_job("fake-complete")
        events = [
            {"version": 1, "event": "started", "stage": "fake"},
            {
                "version": 1,
                "event": "progress",
                "stage": "fake",
                "completed": 2,
                "total": 2,
            },
            {"version": 1, "event": "completed", "stage": "fake"},
        ]
        script = "import json; print('\\n'.join(json.dumps(x) for x in " + repr(events) + "))"
        self.window._queue_command("fake", [sys.executable, "-c", script])
        self.window._run_next()
        self._wait_until(lambda: self.window.process is None)

        self.assertEqual(
            self.window.store.get_job("fake-complete").status,
            JobStatus.COMPLETED,
        )
        stage = self.window.store.get_stage("fake-complete", "fake")
        assert stage is not None
        self.assertEqual(stage.status, StageStatus.COMPLETED)
        self.assertEqual((stage.completed, stage.total), (2, 2))

    def test_fake_worker_can_be_cancelled(self) -> None:
        self._create_job("fake-cancel")
        event = json.dumps({"version": 1, "event": "started", "stage": "fake"})
        script = f"import time; print({event!r}, flush=True); time.sleep(30)"
        self.window._queue_command("fake", [sys.executable, "-c", script])
        self.window._run_next()
        self._wait_until(lambda: self.window._process_is_running())
        self.assertTrue(self.window.cancel_button.isEnabled())
        self.window.cancel_button.click()
        self._wait_until(lambda: self.window.process is None)
        self.assertEqual(
            self.window.store.get_job("fake-cancel").status,
            JobStatus.CANCELLED,
        )
        self.assertIn("durduruldu", self.window.status.text().casefold())

    def test_reopen_and_ssd_wait_are_visible_in_history(self) -> None:
        self._create_job("fake-reopen")
        self.window.store.ensure_stages("fake-reopen", ["fake"])
        self.window.store.transition_job("fake-reopen", JobStatus.RUNNING)
        self.window.store.set_stage("fake-reopen", "fake", StageStatus.RUNNING)
        self.window.close()

        self.window = MainWindow()
        self.window.workspace_initialized = True
        self.window._workspace_resolution_finished(self.workspace, None, True)
        self.assertEqual(
            self.window.store.get_job("fake-reopen").status,
            JobStatus.QUEUED,
        )
        self.assertEqual(self.window.history.rowCount(), 1)

        self.resolve_workspace.side_effect = RuntimeError("SSD yok")
        for _ in range(3):
            self.window._poll_workspace()
            self._wait_until(lambda: not self.window.workspace_resolution_pending)
        self.assertEqual(
            self.window.store.get_job("fake-reopen").status,
            JobStatus.WAITING_FOR_SSD,
        )
        self.resolve_workspace.side_effect = None
        self.resolve_workspace.return_value = self.workspace
        self.window._poll_workspace()
        self._wait_until(lambda: not self.window.workspace_resolution_pending)
        self.assertEqual(
            self.window.store.get_job("fake-reopen").status,
            JobStatus.QUEUED,
        )

    def test_startup_resumes_only_the_job_that_was_interrupted(self) -> None:
        self._create_job("fake-auto-resume")
        self.window.store.ensure_stages("fake-auto-resume", ["translate"])
        self.window.store.transition_job(
            "fake-auto-resume", JobStatus.RUNNING, current_stage="translate"
        )
        self.window.store.set_stage(
            "fake-auto-resume", "translate", StageStatus.RUNNING
        )
        self.window.startup_interrupted_job_ids = ("fake-auto-resume",)

        with patch.object(self.window, "_start") as start:
            self.window._workspace_resolution_finished(self.workspace, None, True)
            self._wait_until(lambda: start.call_count == 1)

        resumed = start.call_args.kwargs["existing_job"]
        self.assertEqual(resumed.id, "fake-auto-resume")
        self.assertEqual(resumed.status, JobStatus.QUEUED)
        self.assertEqual(self.window.startup_interrupted_job_ids, ())

    def test_transient_identity_probe_failure_does_not_stop_active_ssd_job(self) -> None:
        self._create_job("fake-transient-ssd")
        event = json.dumps({"version": 1, "event": "started", "stage": "fake"})
        script = f"import time; print({event!r}, flush=True); time.sleep(30)"
        self.window._queue_command("fake", [sys.executable, "-c", script])
        self.window._run_next()
        self._wait_until(lambda: self.window._process_is_running())

        self.resolve_workspace.side_effect = RuntimeError("diskutil geçici olarak yanıt vermedi")
        for _ in range(4):
            self.window._poll_workspace()
            self._wait_until(lambda: not self.window.workspace_resolution_pending)

        self.assertTrue(self.window._process_is_running())
        self.assertIsNotNone(self.window.workspace)
        self.assertEqual(
            self.window.store.get_job("fake-transient-ssd").status,
            JobStatus.RUNNING,
        )
        self.window._cancel()
        self._wait_until(lambda: not self.window._process_is_running())


if __name__ == "__main__":
    unittest.main()
