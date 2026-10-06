from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ksi_local.downloader import (
    BrowserSession,
    DownloaderError,
    FailureCategory,
    SourceURLValidationError,
    _raise_download_error,
    build_download_plan,
    build_probe_plan,
    classify_failure,
    validate_source_url,
)
from ksi_local.gui import MainWindow
from ksi_local.preflight import inspect_source
from ksi_local.settings import WorkspacePaths
from ksi_local.storage import GIB
from ksi_local.subtitles import convert_text_source_to_srt, parse_vtt_text, read_srt


def _workspace(root: Path) -> WorkspacePaths:
    workspace_root = root / "KSI-Workspace"
    workspace_root.mkdir(parents=True)
    (workspace_root / "jobs").mkdir()
    return WorkspacePaths(
        root=workspace_root,
        jobs=workspace_root / "jobs",
        outputs=workspace_root / "outputs",
        models_ollama=workspace_root / "models/ollama",
        models_whisper=workspace_root / "models/whisper",
        yt_dlp=workspace_root / "tools/yt-dlp/2026.08.19/yt-dlp",
        deno=workspace_root / "tools/deno/2.9.6/deno",
    )


class UdemyURLPolicyTests(unittest.TestCase):
    def test_modern_single_lecture_is_canonicalized_and_query_removed(self) -> None:
        source = validate_source_url(
            "https://www.udemy.com/course/safe-course/learn/lecture/12345"
            "?start=10#overview"
        )
        self.assertEqual(
            source.url,
            "https://www.udemy.com/safe-course/learn/v4/t/lecture/12345",
        )

    def test_course_and_non_numeric_lecture_are_rejected(self) -> None:
        for url in (
            "https://www.udemy.com/course/safe-course/",
            "https://www.udemy.com/course/safe-course/learn/",
            "https://www.udemy.com/course/safe-course/learn/lecture/all",
        ):
            with self.subTest(url=url), self.assertRaises(SourceURLValidationError):
                validate_source_url(url)

    def test_udemy_requires_confirmation_and_dedicated_session(self) -> None:
        url = "https://www.udemy.com/course/safe-course/learn/lecture/12345"
        with self.assertRaisesRegex(ValueError, "erişim hakkı"):
            build_probe_plan(url)
        with self.assertRaisesRegex(ValueError, "ayrı tarayıcı"):
            build_probe_plan(url, udemy_access_confirmed=True)

        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "KSI Local Studio-Udemy"
            profile.mkdir()
            session = BrowserSession("chrome", profile)
            probe = build_probe_plan(
                url,
                browser_session=session,
                udemy_access_confirmed=True,
            )
            download = build_download_plan(
                url,
                output_directory=directory,
                yt_dlp_path="/tools/yt-dlp",
                browser_session=session,
                udemy_access_confirmed=True,
            )
        for argv in (probe.argv, download.argv):
            self.assertIn("--no-playlist", argv)
            self.assertIn("--cookies-from-browser", argv)
            self.assertIn("--sleep-requests", argv)
            self.assertNotIn("--username", argv)
            self.assertNotIn("--password", argv)
            self.assertNotIn("--cookies", argv)
        self.assertEqual(download.argv[download.argv.index("--retries") + 1], "2")
        self.assertNotIn(str(profile), str(download.display_dict()))

    def test_secure_failure_taxonomy_covers_udemy_stops(self) -> None:
        self.assertIs(classify_failure("Please solve CAPTCHA"), FailureCategory.CAPTCHA)
        self.assertIs(
            classify_failure("ERROR: Unable to extract course id"),
            FailureCategory.COURSE_ID,
        )
        self.assertIs(classify_failure("This lecture is DRM protected"), FailureCategory.DRM)
        with self.assertRaises(DownloaderError) as raised:
            _raise_download_error(
                "ERROR: Please solve CAPTCHA",
                "https://www.udemy.com/safe/learn/v4/t/lecture/1",
                "failed",
            )
        self.assertIs(raised.exception.category, FailureCategory.CAPTCHA)
        self.assertIn("aşıl", str(raised.exception))
        self.assertIn("MP4/SRT/VTT/TXT", str(raised.exception))


class UdemyPreflightAndFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.workspace = _workspace(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_preflight_is_experimental_authenticated_and_single_lecture(self) -> None:
        profile = self.root / "KSI Local Studio-Udemy-Profile"
        profile.mkdir()
        payload = {
            "id": "12345",
            "title": "Authorized lecture",
            "duration": 90,
            "availability": "needs_auth",
            "formats": [
                {
                    "height": 720,
                    "vcodec": "h264",
                    "acodec": "aac",
                    "filesize": 2_000_000,
                }
            ],
            "subtitles": {"en": [{}]},
        }
        tools = SimpleNamespace(
            tools=(SimpleNamespace(name="yt-dlp", expected_version="2026.08.19"),)
        )
        with (
            patch("ksi_local.preflight.verify_download_tools", return_value=tools),
            patch("ksi_local.preflight.run_probe_with_retry", return_value=(payload, 1)),
            patch(
                "ksi_local.preflight.shutil.disk_usage",
                return_value=SimpleNamespace(free=60 * GIB),
            ),
        ):
            result = inspect_source(
                "https://www.udemy.com/course/safe/learn/lecture/12345",
                workspace=self.workspace,
                download_only=True,
                want_subtitle=False,
                want_summary=False,
                browser_session=BrowserSession("chrome", profile),
                udemy_access_confirmed=True,
            )
        self.assertEqual(result.platform, "Udemy (Deneysel)")
        self.assertTrue(result.requires_authentication)
        self.assertTrue(result.session_used)
        self.assertTrue(any("DRM" in warning for warning in result.warnings))

    def test_vtt_and_plain_transcript_are_local_fallbacks(self) -> None:
        vtt = self.root / "lecture.vtt"
        vtt.write_text(
            "WEBVTT\n\n00:01.000 --> 00:03.500\nHello world\n",
            encoding="utf-8",
        )
        cues = parse_vtt_text(vtt.read_text(encoding="utf-8"))
        self.assertEqual((cues[0].start, cues[0].end), ("00:00:01,000", "00:00:03,500"))
        converted = self.root / "converted.srt"
        convert_text_source_to_srt(vtt, converted)
        self.assertEqual(read_srt(converted)[0].text, "Hello world")

        transcript = self.root / "lecture.txt"
        transcript.write_text("First paragraph.\n\nSecond paragraph.", encoding="utf-8")
        result = inspect_source(
            str(transcript),
            workspace=self.workspace,
            download_only=False,
            want_subtitle=False,
            want_summary=True,
        )
        self.assertEqual(result.source_kind, "subtitle")
        self.assertIn("yaklaşık zamanlar", result.warnings[0])


class PhaseNineGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.environment = patch.dict(
            os.environ, {"KSI_STATE_DIRECTORY": str(root / "state")}
        )
        self.environment.start()
        self.workspace = _workspace(root)
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

    def test_udemy_controls_are_explicit_and_worker_gets_confirmation(self) -> None:
        self.window.source.setText(
            "https://www.udemy.com/course/safe/learn/lecture/12345"
        )
        self.assertTrue(self.window.advanced_session_panel.isHidden())
        self.window.advanced_session_toggle.setChecked(True)
        self.assertFalse(self.window.advanced_session_panel.isHidden())
        self.assertFalse(self.window.udemy_access_confirmed.isHidden())
        self.assertTrue(self.window.select_profile_button.isEnabled())
        self.window.udemy_access_confirmed.setChecked(True)
        profile = Path(self.temporary.name) / "KSI Local Studio-Udemy"
        profile.mkdir()
        session = BrowserSession("chrome", profile)
        self.window._prepare_job(
            self.window.source.text(),
            None,
            max_height=720,
            browser_session=session,
        )
        command = self.window.pending[0].argv
        self.assertIn("--confirm-udemy-access", command)
        self.assertIn("--browser-profile", command)


if __name__ == "__main__":
    unittest.main()
