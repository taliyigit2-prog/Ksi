from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialogButtonBox

from ksi_local.gui import MainWindow, PreflightDialog
from ksi_local.preflight import FormatChoice, PreflightResult
from ksi_local.settings import WorkspacePaths


def preflight_result() -> PreflightResult:
    return PreflightResult(
        source_kind="youtube",
        platform="YouTube",
        title="Example",
        uploader="Channel",
        duration_seconds=120,
        live_status="not_live",
        entry_count=1,
        manual_subtitles=("en",),
        automatic_subtitles=("en", "tr"),
        detected_language="en",
        requires_authentication=False,
        available_heights=(720, 1080),
        default_height=1080,
        format_choices=(
            FormatChoice(720, 100, 200, 300, True),
            FormatChoice(1080, 200, 400, 500, True),
        ),
        fixed_budget=None,
        requested_outputs=("Sadece indir",),
        free_bytes=1000,
        retry_count=0,
        warnings=(),
        tool_versions=("yt-dlp 2026.08.19",),
    )


class GuiPhase3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.environment = patch.dict(
            os.environ,
            {"KSI_STATE_DIRECTORY": str(root / "state")},
        )
        self.environment.start()
        workspace_root = root / "KSI-Workspace"
        self.workspace = WorkspacePaths(
            root=workspace_root,
            jobs=workspace_root / "jobs",
            outputs=workspace_root / "outputs",
            models_ollama=workspace_root / "models/ollama",
            models_whisper=workspace_root / "models/whisper",
            yt_dlp=workspace_root / "tools/yt-dlp/2026.08.19/yt-dlp",
            deno=workspace_root / "tools/deno/2.9.6/deno",
        )
        self.workspace.jobs.mkdir(parents=True)
        self.resolver = patch("ksi_local.gui.resolve_workspace", return_value=self.workspace)
        self.resolver.start()
        self.window = MainWindow()
        self.window.workspace_initialized = True
        self.window._workspace_resolution_finished(self.workspace, None, True)

    def tearDown(self) -> None:
        self.window.close()
        self.resolver.stop()
        self.environment.stop()
        self.temporary.cleanup()

    def test_only_download_is_mutually_exclusive(self) -> None:
        self.window.download_only.setChecked(True)
        self.assertFalse(self.window.want_subtitle.isChecked())
        self.assertFalse(self.window.want_summary.isChecked())
        self.assertFalse(self.window.want_subtitle.isEnabled())
        self.assertEqual(self.window.start_button.text(), "İncele ve Başlat")

    def test_dialog_returns_selected_height_and_shows_budget(self) -> None:
        dialog = PreflightDialog(preflight_result())
        dialog.height_combo.setCurrentIndex(dialog.height_combo.findData(720))
        self.assertEqual(dialog.selected_height, 720)
        self.assertIn("20 GiB", dialog.budget_label.text())
        self.assertTrue(
            dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
        )
        dialog.close()

    def test_only_download_command_has_no_subtitle_argument(self) -> None:
        self.window.source.setText("https://www.youtube.com/watch?v=example")
        self.window.download_only.setChecked(True)
        result = preflight_result()
        self.window._prepare_job(
            self.window.source.text(),
            None,
            max_height=720,
            preflight=result,
        )
        self.assertEqual(len(self.window.pending), 1)
        command = self.window.pending[0].argv
        self.assertIn("--max-height", command)
        self.assertEqual(command[command.index("--max-height") + 1], "720")
        self.assertNotIn("--subtitle-language", command)
        record = self.window.store.get_job(self.window.current_job_id or "")
        self.assertTrue(record.download_only)
        self.assertEqual(record.max_height, 720)
        self.assertTrue((Path(record.job_directory) / "manifest.json").is_file())


if __name__ == "__main__":
    unittest.main()
