from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialogButtonBox

from ksi_local.downloader import (
    DownloaderError,
    FailureCategory,
    build_download_plan,
    build_probe_plan,
    run_download_with_retry,
    validate_browser_session,
)
from ksi_local.gui import PreflightDialog
from ksi_local.job_store import JobStore
from ksi_local.preflight import inspect_source
from ksi_local.privacy import redact_sensitive_text
from ksi_local.settings import WorkspacePaths
from ksi_local.storage import GIB


def _x_video(identifier: str, title: str, duration: float = 30) -> dict:
    return {
        "id": identifier,
        "title": title,
        "duration": duration,
        "language": "es",
        "formats": [
            {
                "height": height,
                "vcodec": "h264",
                "acodec": "aac",
                "filesize": height * 10_000,
            }
            for height in (270, 720, 1080, 2160)
        ],
        "subtitles": {"en": [{}]},
    }


class XDownloadPolicyTests(unittest.TestCase):
    def test_public_x_uses_guest_graphql_and_bounded_backoff(self) -> None:
        probe = build_probe_plan("https://x.com/example/status/1")
        self.assertIn("twitter:api=graphql", probe.argv)
        self.assertIn("http:exp=5:60", probe.argv)
        self.assertIn("--no-cache-dir", probe.argv)
        self.assertNotIn("--cookies-from-browser", probe.argv)

        with tempfile.TemporaryDirectory() as directory:
            download = build_download_plan(
                "https://x.com/example/status/1",
                output_directory=directory,
                yt_dlp_path="/tools/yt-dlp",
                subtitle_languages=(),
                media_index=2,
            )
        self.assertEqual(
            download.argv[download.argv.index("--playlist-items") + 1], "2"
        )
        self.assertEqual(download.argv[download.argv.index("--retries") + 1], "3")

    def test_browser_profile_is_opt_in_and_hidden_from_display(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "KSI Local Studio-X-Profile"
            profile.mkdir()
            session = validate_browser_session("chrome", profile)
            plan = build_probe_plan(
                "https://x.com/example/status/1", browser_session=session
            )
            self.assertIn("--cookies-from-browser", plan.argv)
            shown = json.dumps(plan.display_dict(), ensure_ascii=False)
            self.assertNotIn(str(profile), shown)
            self.assertIn("ayrı-tarayıcı-oturumu", shown)

    def test_main_chrome_profile_is_rejected(self) -> None:
        main = Path.home() / "Library/Application Support/Google/Chrome/Default"
        with patch("ksi_local.downloader.Path.is_dir", return_value=True):
            with self.assertRaisesRegex(ValueError, "Ana tarayıcı profili"):
                validate_browser_session("chrome", main)

    def test_429_is_not_immediately_retried_by_outer_layer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan = build_download_plan(
                "https://x.com/example/status/1",
                output_directory=directory,
                yt_dlp_path="/tools/yt-dlp",
                subtitle_languages=(),
            )
            with patch(
                "ksi_local.downloader.run_download",
                side_effect=DownloaderError("429", FailureCategory.RATE_LIMIT),
            ) as run:
                with self.assertRaises(DownloaderError):
                    run_download_with_retry(plan)
        self.assertEqual(run.call_count, 1)

    def test_403_gets_one_direct_https_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan = build_download_plan(
                "https://x.com/example/status/1",
                output_directory=directory,
                yt_dlp_path="/tools/yt-dlp",
                subtitle_languages=(),
            )
            direct = build_download_plan(
                "https://x.com/example/status/1",
                output_directory=directory,
                yt_dlp_path="/tools/yt-dlp",
                subtitle_languages=(),
                direct_https_only=True,
            )
            with patch(
                "ksi_local.downloader.run_download",
                side_effect=[
                    DownloaderError("403", FailureCategory.FORBIDDEN),
                    [str(Path(directory) / "source.mp4")],
                ],
            ) as run:
                result = run_download_with_retry(
                    plan, direct_fallback_plan=direct
                )
        self.assertEqual(run.call_count, 2)
        self.assertEqual(result.fallback, "direct_https_after_403")


class XMultipleMediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_multi_video_requires_explicit_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = WorkspacePaths(
                root=root,
                jobs=root / "jobs",
                outputs=root / "outputs",
                models_ollama=root / "models/ollama",
                models_whisper=root / "models/whisper",
                yt_dlp=root / "tools/yt-dlp/2026.08.19/yt-dlp",
                deno=root / "tools/deno/2.9.6/deno",
            )
            workspace.models_ollama.mkdir(parents=True)
            workspace.models_whisper.mkdir(parents=True)
            payload = {
                "_type": "playlist",
                "entries": [
                    _x_video("one", "Birinci video", 20),
                    _x_video("two", "İkinci video", 40),
                ],
            }
            tools = SimpleNamespace(
                tools=(SimpleNamespace(name="yt-dlp", expected_version="2026.08.19"),)
            )
            with (
                patch("ksi_local.preflight.verify_download_tools", return_value=tools),
                patch("ksi_local.preflight.run_probe_with_retry", return_value=(payload, 1)),
                patch(
                    "ksi_local.preflight.shutil.disk_usage",
                    return_value=SimpleNamespace(free=75 * GIB),
                ),
            ):
                result = inspect_source(
                    "https://x.com/example/status/1",
                    workspace=workspace,
                    download_only=True,
                    want_subtitle=False,
                    want_summary=False,
                )

        self.assertEqual(result.entry_count, 2)
        self.assertEqual(tuple(item.index for item in result.media_choices), (1, 2))
        dialog = PreflightDialog(result)
        ok = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.assertEqual(dialog.selected_media_index, 0)
        self.assertFalse(ok.isEnabled())
        dialog.media_combo.setCurrentIndex(dialog.media_combo.findData(2))
        self.assertEqual(dialog.selected_media_index, 2)
        self.assertEqual(dialog.title_label.text(), "İkinci video")
        self.assertTrue(ok.isEnabled())
        dialog.close()


class SessionPrivacyTests(unittest.TestCase):
    def test_cookie_tokens_and_profile_argument_are_redacted(self) -> None:
        raw = (
            "--cookies-from-browser chrome:/Users/test/SecretProfile "
            "auth_token=abc ct0=def guest_token=ghi x-csrf-token=jkl"
        )
        clean = redact_sensitive_text(raw)
        for secret in ("SecretProfile", "abc", "def", "ghi", "jkl"):
            self.assertNotIn(secret, clean)

    def test_session_data_is_not_stored_in_job_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "jobs.sqlite3"
            store = JobStore(database)
            store.create_job(
                job_id="x-job",
                source="https://x.com/example/status/1?secret=query",
                source_language="auto",
                want_subtitle=False,
                want_summary=False,
                want_dub=False,
                download_only=True,
                max_height=720,
                media_index=2,
                job_directory=root / "job",
            )
            store.append_log(
                "x-job",
                "--cookies-from-browser chrome:/Users/test/SecretProfile auth_token=abc",
            )
            persisted = database.read_bytes()
            wal = Path(f"{database}-wal")
            if wal.is_file():
                persisted += wal.read_bytes()
        self.assertNotIn(b"SecretProfile", persisted)
        self.assertNotIn(b"auth_token=abc", persisted)


if __name__ == "__main__":
    unittest.main()
