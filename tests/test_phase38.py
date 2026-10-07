from __future__ import annotations

import json
import tempfile
import unittest
import os
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from ksi_local.manual_acceptance import (
    acceptance_cases,
    create_session,
    load_session,
    record_result,
    session_progress,
)
from ksi_local.cli import main

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from unittest.mock import patch
from ksi_local.gui import MainWindow
from ksi_local.preferences import load_preferences


TEST_APP = QApplication.instance() or QApplication([])


class PhaseThirtyEightAcceptanceTests(unittest.TestCase):
    def test_matrix_contains_core_workflows_and_all_55_candidate_locales(self) -> None:
        cases = acceptance_cases()
        self.assertEqual(len([item for item in cases if not item.blocking]), 55)
        keys = {item.key for item in cases}
        for key in ("youtube-video", "youtube-shorts", "x-public", "document", "web-article", "dubbing", "ssd-disconnect", "network-disconnect", "stop-resume", "keyboard"):
            self.assertIn(key, keys)

    def test_new_session_never_overwrites_and_starts_non_releasable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acceptance.json"
            create_session(path)
            with self.assertRaises(FileExistsError):
                create_session(path)
            progress = session_progress(path)
            self.assertFalse(progress.releasable)
            self.assertEqual(progress.blocking_pending, progress.blocking_total)

    def test_new_session_rejects_a_dangling_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            referent = root / "private.json"
            link = root / "acceptance.json"
            link.symlink_to(referent)
            with self.assertRaises(FileExistsError):
                create_session(link)
            self.assertFalse(referent.exists())

    def test_pass_requires_all_human_scores_at_least_four(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acceptance.json"
            create_session(path)
            with self.assertRaises(ValueError):
                record_result(path, "dubbing", status="passed", ratings={"meaning": 5})
            with self.assertRaises(ValueError):
                record_result(path, "dubbing", status="passed", ratings={"meaning": 5, "naturalness": 3, "pronunciation": 5})
            record_result(path, "dubbing", status="passed", ratings={"meaning": 5, "naturalness": 4, "pronunciation": 4})
            self.assertEqual(load_session(path)["cases"]["dubbing"]["status"], "passed")

    def test_failure_is_reproducible_and_sensitive_text_is_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acceptance.json"
            create_session(path)
            record_result(
                path,
                "web-article",
                status="failed",
                note="https://example.com/story?token=secret",
                expected="Sayfa açılsın",
                actual="password=hunter2",
                reproduction_steps=("Bearer abcdef", "Tekrar çalıştır"),
            )
            payload = load_session(path)
            serialized = str(payload)
            self.assertNotIn("hunter2", serialized)
            self.assertNotIn("abcdef", serialized)
            self.assertNotIn("token=secret", serialized)
            self.assertTrue(payload["failures"][0]["id"].startswith("KSI-ACCEPT-"))

    def test_acceptance_notes_redact_home_and_external_volume_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acceptance.json"
            create_session(path)
            record_result(
                path,
                "app-reopen",
                status="failed",
                actual=(
                    f"{Path.home()}/Documents/private.txt ve "
                    "/Volumes/PrivateSSD/KSI-Workspace/jobs/customer/source.txt"
                ),
            )
            serialized = json.dumps(load_session(path), ensure_ascii=False)
            self.assertNotIn(str(Path.home()), serialized)
            self.assertNotIn("PrivateSSD", serialized)
            self.assertNotIn("customer/source.txt", serialized)

    def test_cli_can_create_record_and_report_without_source_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "acceptance.json"
            with redirect_stdout(StringIO()):
                self.assertEqual(main(["acceptance-create", str(path)]), 0)
                self.assertEqual(
                    main([
                        "acceptance-record", str(path), "app-reopen", "passed",
                        "--rating", "ui=5", "--rating", "usability=4",
                    ]),
                    0,
                )
                self.assertEqual(main(["acceptance-status", str(path)]), 4)
            self.assertEqual(load_session(path)["cases"]["app-reopen"]["status"], "passed")

    def test_professional_tabs_help_theme_and_clear_kind_selection_persist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            with (
                patch.dict(os.environ, {"KSI_STATE_DIRECTORY": str(state)}),
                patch.object(MainWindow, "_initialize_workspace", return_value=None),
            ):
                window = MainWindow()
                self.assertEqual(window.tabs.count(), 8)
                self.assertEqual(
                    [window.tabs.tabText(index) for index in range(5)],
                    ["Yeni İş", "İşlem", "Geçmiş", "Sistem", "Yardım"],
                )
                self.assertEqual(set(window.studio_shell.routes),
                                 {"download", "video", "document", "images", "queue", "history", "library", "help", "settings"})
                self.assertEqual(len(window.help_labels), 5)
                window.video_card.click()
                self.assertTrue(window.video_card.isChecked())
                self.assertIn("✓", window.video_card.text())
                window.video_card.click()
                self.assertTrue(window.video_card.isChecked())
                window.theme.setCurrentIndex(window.theme.findData("light"))
                TEST_APP.processEvents()
                self.assertEqual(load_preferences().theme, "light")
                self.assertEqual(window.theme.currentData(), "light")
                window.close()

    def test_workspace_ready_replaces_loading_page_without_first_run_popup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            with (
                patch.dict(os.environ, {"KSI_STATE_DIRECTORY": str(state)}),
                patch.object(MainWindow, "_initialize_workspace", return_value=None),
                patch.object(MainWindow, "_show_system_status") as show_status,
            ):
                window = MainWindow()
                workspace = type("Workspace", (), {"root": Path(directory)})()
                window._workspace_resolution_finished(workspace, None, True)
                self.assertIs(window.page_stack.currentWidget(), window.content_page)
                show_status.assert_not_called()
                self.assertFalse(window.first_run_pending)
                window.close()


if __name__ == "__main__":
    unittest.main()
