from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ksi_local.gui import MainWindow
from ksi_local.job_store import JobKind, JobStatus
from ksi_local.preferences import load_preferences, save_preferences


class PhaseTwentyFiveHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def _window(self, root: Path) -> MainWindow:
        state = root / "state"
        jobs = root / "KSI-Workspace" / "jobs"
        jobs.mkdir(parents=True)
        environment = patch.dict(os.environ, {"KSI_STATE_DIRECTORY": str(state)})
        initialize = patch.object(MainWindow, "_initialize_workspace", return_value=None)
        environment.start()
        initialize.start()
        self.addCleanup(environment.stop)
        self.addCleanup(initialize.stop)
        window = MainWindow()
        window.workspace = SimpleNamespace(jobs=jobs, root=jobs.parent)
        return window

    @staticmethod
    def _create_job(window: MainWindow, jobs: Path, index: int, *, source: str) -> str:
        job_id = f"job-{index:04d}-0123456789abcdef"
        job_directory = jobs / job_id
        (job_directory / "outputs").mkdir(parents=True)
        window.store.create_job(
            job_id=job_id,
            source=source,
            source_language="en",
            want_subtitle=True,
            want_summary=False,
            want_dub=False,
            job_directory=job_directory,
            job_kind=JobKind.VIDEO,
        )
        return job_id

    def test_long_history_search_filter_and_keyboard_copy_are_safe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            window = self._window(root)
            jobs = window.workspace.jobs
            for index in range(120):
                self._create_job(
                    window,
                    jobs,
                    index,
                    source=(
                        f"https://www.youtube.com/watch?v=video{index}"
                        f"&token=secret-{index}&password=hidden"
                    ),
                )
            target_id = "job-0119-0123456789abcdef"
            stored = window.store.get_job(target_id)
            self.assertEqual(
                stored.source_reference,
                "https://www.youtube.com/watch?v=video119",
            )
            window.store.append_log(
                target_id,
                "cookie=session-secret https://example.test/watch?token=log-secret",
            )
            persisted_log = "\n".join(window.store.read_logs(target_id))
            self.assertNotIn("session-secret", persisted_log)
            self.assertNotIn("log-secret", persisted_log)
            window._refresh_history()
            self.assertEqual(window.history.rowCount(), 120)
            window.history_search.setText("video119")
            self.application.processEvents()
            self.assertEqual(window.history.rowCount(), 1)
            self.assertNotIn("secret", window.history.item(0, 1).toolTip())
            self.assertNotIn("password", window.history.item(0, 1).toolTip())

            window.history.setCurrentCell(0, 1)
            window.show()
            self.application.processEvents()
            window.history.setFocus()
            self.assertEqual(
                window.history_copy_shortcut.key().matches(
                    QKeySequence(QKeySequence.StandardKey.Copy)
                ),
                QKeySequence.SequenceMatch.ExactMatch,
            )
            QTest.keyClick(
                window.history,
                Qt.Key.Key_C,
                Qt.KeyboardModifier.ControlModifier,
            )
            self.application.processEvents()
            copied = QApplication.clipboard().text()
            self.assertEqual(copied, "https://www.youtube.com/watch?v=video119")
            self.assertNotIn("secret", copied)
            window.history.setCurrentCell(0, 0)
            window._copy_selected_history_cell()
            self.assertEqual(QApplication.clipboard().text(), target_id)

            window.history_filter.setCurrentIndex(
                window.history_filter.findData("completed")
            )
            self.application.processEvents()
            self.assertEqual(window.history.rowCount(), 0)
            window.close()

    def test_source_output_final_video_and_last_export_open_safely(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            window = self._window(root)
            jobs = window.workspace.jobs
            source = root / "source.mp4"
            source.write_bytes(b"source")
            job_id = self._create_job(window, jobs, 1, source=str(source))
            job = window.store.get_job(job_id)
            window.store.transition_job(job_id, JobStatus.RUNNING)
            window.store.transition_job(job_id, JobStatus.COMPLETED)
            final_video = Path(job.job_directory) / "outputs" / "turkce-altyazili.mp4"
            final_video.write_bytes(b"final")
            export = root / "Exported Result"
            export.mkdir()
            window.preferences = replace(
                window.preferences, last_export_directory=str(export)
            )
            save_preferences(window.preferences)
            self.assertEqual(load_preferences().last_export_directory, str(export))
            window._refresh_history()
            window.history.selectRow(0)

            opened: list[QUrl] = []
            with patch(
                "ksi_local.gui.QDesktopServices.openUrl",
                side_effect=lambda url: opened.append(url) or True,
            ):
                window._open_selected_source()
                window._open_selected_output_folder()
                window._open_selected_final_video()
                window._open_last_export_folder()

            self.assertEqual(len(opened), 4)
            self.assertEqual(Path(opened[0].toLocalFile()), source.resolve())
            self.assertEqual(Path(opened[1].toLocalFile()), final_video.parent.resolve())
            self.assertEqual(Path(opened[2].toLocalFile()), final_video.resolve())
            self.assertEqual(Path(opened[3].toLocalFile()), export.resolve())
            window.close()

    def test_legacy_sensitive_url_is_redacted_before_display_copy_or_open(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            window = self._window(root)
            jobs = window.workspace.jobs
            job_id = self._create_job(
                window,
                jobs,
                2,
                source="https://x.com/example/status/123",
            )
            with window.store._connect() as connection:
                connection.execute(
                    "UPDATE jobs SET source_reference = ? WHERE id = ?",
                    (
                        "https://user:password@x.com/example/status/123?token=top-secret#private",
                        job_id,
                    ),
                )
            window._refresh_history()
            window.history.selectRow(0)
            displayed = window.history.item(0, 1).text()
            self.assertEqual(displayed, "https://x.com/example/status/123")
            window._copy_selected_source()
            self.assertEqual(QApplication.clipboard().text(), displayed)
            with patch("ksi_local.gui.QDesktopServices.openUrl", return_value=True) as opener:
                window._open_selected_source()
            opened = opener.call_args.args[0].toString()
            self.assertEqual(opened, displayed)
            self.assertNotIn("password", opened)
            self.assertNotIn("token", opened)
            window.close()

    def test_context_menu_exposes_safe_source_id_and_result_actions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            window = self._window(root)
            jobs = window.workspace.jobs
            self._create_job(
                window,
                jobs,
                3,
                source="https://www.youtube.com/watch?v=safe-id&token=private",
            )
            window._refresh_history()
            window.history.selectRow(0)
            menu = window._history_context_menu()
            assert menu is not None
            labels = {action.text() for action in menu.actions() if action.text()}
            self.assertIn(window._t("history.copy_link"), labels)
            self.assertIn(window._t("history.open_source"), labels)
            self.assertIn(window._t("history.copy_id"), labels)
            self.assertIn(window._t("history.open_output"), labels)
            self.assertNotIn("private", " ".join(labels))
            window.close()


if __name__ == "__main__":
    unittest.main()
