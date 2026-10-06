from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ksi_local.downloader import (
    DownloaderError,
    FailureCategory,
    build_download_plan,
    build_probe_plan,
    classify_failure,
    estimate_download_bytes,
    run_probe,
    run_probe_with_retry,
)
from ksi_local.job_store import JobStore, SCHEMA_VERSION
from ksi_local.preflight import inspect_source
from ksi_local.settings import WorkspacePaths
from ksi_local.storage import GIB
from ksi_local.tool_integrity import verify_download_tools
from ksi_local.tool_integrity import _version_output


class DownloadPolicyTests(unittest.TestCase):
    def test_selected_height_is_bounded_and_used(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan = build_download_plan(
                "https://youtu.be/example",
                output_directory=directory,
                yt_dlp_path="/tools/yt-dlp",
                max_height=720,
            )
            format_selector = plan.argv[plan.argv.index("--format") + 1]
            self.assertIn("bv*[height<=720][vcodec^=avc1]", format_selector)
            with self.assertRaises(ValueError):
                build_download_plan(
                    "https://youtu.be/example",
                    output_directory=directory,
                    yt_dlp_path="/tools/yt-dlp",
                    max_height=2160,
                )

    def test_download_only_omits_all_subtitle_requests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan = build_download_plan(
                "https://youtu.be/example",
                output_directory=directory,
                yt_dlp_path="/tools/yt-dlp",
                subtitle_languages=(),
            )
        self.assertNotIn("--write-subs", plan.argv)
        self.assertNotIn("--write-auto-subs", plan.argv)
        self.assertNotIn("--sub-langs", plan.argv)

    def test_estimates_best_video_plus_audio(self) -> None:
        payload = {
            "duration": 100,
            "formats": [
                {
                    "height": 720,
                    "vcodec": "vp9",
                    "acodec": "none",
                    "filesize": 80_000_000,
                    "fps": 30,
                },
                {
                    "height": 1080,
                    "vcodec": "vp9",
                    "acodec": "none",
                    "filesize": 140_000_000,
                    "fps": 30,
                },
                {
                    "vcodec": "none",
                    "acodec": "opus",
                    "filesize": 10_000_000,
                    "abr": 128,
                },
            ],
        }
        self.assertEqual(estimate_download_bytes(payload, 720), 90_000_000)
        self.assertEqual(estimate_download_bytes(payload, 1080), 150_000_000)

    def test_failure_categories_stop_auth_and_drm(self) -> None:
        self.assertEqual(
            classify_failure("Sign in to confirm your age"), FailureCategory.AUTH_REQUIRED
        )
        self.assertEqual(classify_failure("This video is DRM protected"), FailureCategory.DRM)
        self.assertEqual(classify_failure("HTTP Error 503"), FailureCategory.TRANSIENT)
        self.assertEqual(classify_failure("PO Token required"), FailureCategory.PO_TOKEN)

    def test_probe_retries_only_one_retryable_failure(self) -> None:
        plan = build_probe_plan("https://youtu.be/example")
        payload = {"id": "example"}
        with patch(
            "ksi_local.downloader.run_probe",
            side_effect=[
                DownloaderError("temporary", FailureCategory.TRANSIENT),
                payload,
            ],
        ) as mocked:
            actual, attempts = run_probe_with_retry(plan)
        self.assertEqual(actual, payload)
        self.assertEqual(attempts, 2)
        self.assertEqual(mocked.call_count, 2)

    def test_probe_timeout_becomes_a_retryable_safe_error(self) -> None:
        plan = build_probe_plan("https://youtu.be/example")
        with patch(
            "ksi_local.downloader.subprocess.run",
            side_effect=subprocess.TimeoutExpired(plan.argv, 1),
        ):
            with self.assertRaises(DownloaderError) as captured:
                run_probe(plan, timeout_seconds=1)
        self.assertEqual(captured.exception.category, FailureCategory.TRANSIENT)
        self.assertNotIn("youtu.be", str(captured.exception))


class ToolIntegrityTests(unittest.TestCase):
    def test_cold_external_tool_gets_a_long_enough_version_timeout(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["yt-dlp", "--version"],
            returncode=0,
            stdout="2026.08.19\n",
            stderr="",
        )
        with patch("ksi_local.tool_integrity.subprocess.run", return_value=completed) as mocked:
            output = _version_output(Path("yt-dlp"), "--version")
        self.assertEqual(output, "2026.08.19")
        self.assertEqual(mocked.call_args.kwargs["timeout"], 45)

    def test_hash_and_version_are_both_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def executable(name: str, output: str) -> Path:
                target = root / name
                target.write_text(f"#!/bin/sh\necho '{output}'\n", encoding="utf-8")
                target.chmod(0o700)
                return target

            yt_dlp = executable("yt-dlp", "2026.08.19")
            deno = executable("deno", "deno 2.9.6")
            ffmpeg = executable("ffmpeg", "ffmpeg version 9.0.1")
            ffprobe = executable("ffprobe", "ffprobe version 9.0.1")

            def digest(path: Path) -> str:
                return hashlib.sha256(path.read_bytes()).hexdigest()

            manifest = root / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "tools": {
                            "yt-dlp": {
                                "version": "2026.08.19",
                                "sha256": digest(yt_dlp),
                            },
                            "deno": {
                                "version": "2.9.6",
                                "binary_sha256": digest(deno),
                            },
                            "ffmpeg": {"version": "9.0.1"},
                            "ffprobe": {"version": "9.0.1"},
                        },
                    }
                ),
                encoding="utf-8",
            )
            report = verify_download_tools(
                yt_dlp_path=yt_dlp,
                deno_path=deno,
                ffmpeg_path=ffmpeg,
                ffprobe_path=ffprobe,
                path=manifest,
            )
            self.assertTrue(all(item.digest_verified is not False for item in report.tools))
            yt_dlp.write_text("#!/bin/sh\necho '2026.08.19'\n# changed\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                verify_download_tools(
                    yt_dlp_path=yt_dlp,
                    deno_path=deno,
                    ffmpeg_path=ffmpeg,
                    ffprobe_path=ffprobe,
                    path=manifest,
                )


class PreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.workspace = WorkspacePaths(
            root=root,
            jobs=root / "jobs",
            outputs=root / "outputs",
            models_ollama=root / "models/ollama",
            models_whisper=root / "models/whisper",
            yt_dlp=root / "tools/yt-dlp/2026.08.19/yt-dlp",
            deno=root / "tools/deno/2.9.6/deno",
        )
        self.workspace.models_ollama.mkdir(parents=True)
        self.workspace.models_whisper.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_youtube_preflight_offers_only_up_to_1080p(self) -> None:
        payload = {
            "id": "example",
            "title": "Allowed example",
            "channel": "Example channel",
            "duration": 120,
            "live_status": "not_live",
            "availability": "public",
            "formats": [
                {
                    "height": height,
                    "vcodec": "vp9",
                    "acodec": "opus",
                    "filesize": height * 100_000,
                    "fps": 30,
                }
                for height in (360, 720, 1080, 2160)
            ],
            "subtitles": {"en": [{}], "ja": [{}]},
            "automatic_captions": {"es": [{}], "tr": [{}]},
        }
        tools = SimpleNamespace(
            tools=(SimpleNamespace(name="yt-dlp", expected_version="2026.08.19"),)
        )
        fake_disk = SimpleNamespace(free=75 * GIB)
        with (
            patch("ksi_local.preflight.verify_download_tools", return_value=tools),
            patch("ksi_local.preflight.run_probe_with_retry", return_value=(payload, 1)),
            patch("ksi_local.preflight.shutil.disk_usage", return_value=fake_disk),
        ):
            result = inspect_source(
                "https://www.youtube.com/watch?v=example",
                workspace=self.workspace,
                download_only=True,
                want_subtitle=False,
                want_summary=False,
            )
        self.assertEqual(result.available_heights, (360, 720, 1080))
        self.assertEqual(result.default_height, 1080)
        self.assertEqual(result.manual_subtitles, ("en",))
        self.assertEqual(result.automatic_subtitles, ("es", "tr"))
        self.assertIsNone(result.detected_language)
        self.assertEqual(result.requested_outputs, ("Sadece indir",))

    def test_x_is_available_after_phase_four(self) -> None:
        payload = {
            "id": "x-example",
            "title": "Public X video",
            "duration": 20,
            "formats": [
                {
                    "height": 720,
                    "vcodec": "h264",
                    "acodec": "aac",
                    "filesize": 2_000_000,
                }
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
                workspace=self.workspace,
                download_only=True,
                want_subtitle=False,
                want_summary=False,
            )
        self.assertEqual(result.platform, "X")
        self.assertEqual(result.entry_count, 1)

    def test_download_only_rejects_a_local_no_op(self) -> None:
        local = self.workspace.root / "video.mp4"
        local.write_bytes(b"not read because the mode is rejected first")
        with self.assertRaisesRegex(ValueError, "zaten bilgisayarınızda"):
            inspect_source(
                str(local),
                workspace=self.workspace,
                download_only=True,
                want_subtitle=False,
                want_summary=False,
            )


class SchemaMigrationTests(unittest.TestCase):
    def test_version_one_database_gains_phase_three_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "jobs.sqlite3"
            with sqlite3.connect(database) as connection:
                connection.executescript(
                    """
                    CREATE TABLE jobs (
                        id TEXT PRIMARY KEY, source_kind TEXT NOT NULL,
                        source_reference TEXT NOT NULL, source_language TEXT NOT NULL,
                        want_subtitle INTEGER NOT NULL, want_summary INTEGER NOT NULL,
                        want_dub INTEGER NOT NULL, job_directory TEXT NOT NULL,
                        status TEXT NOT NULL, current_stage TEXT, last_error TEXT,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    PRAGMA user_version = 1;
                    """
                )
            store = JobStore(database)
            store.create_job(
                job_id="phase3",
                source="https://youtu.be/example",
                source_language="auto",
                want_subtitle=False,
                want_summary=False,
                want_dub=False,
                job_directory=Path(directory) / "job",
                download_only=True,
                max_height=720,
            )
            record = store.get_job("phase3")
            self.assertTrue(record.download_only)
            self.assertEqual(record.max_height, 720)
            self.assertEqual(record.media_index, 1)
            with sqlite3.connect(database) as connection:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
            self.assertEqual(version, SCHEMA_VERSION)

    def test_future_database_is_not_silently_downgraded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "future.sqlite3"
            with sqlite3.connect(database) as connection:
                connection.execute("PRAGMA user_version = 999")
            with self.assertRaisesRegex(RuntimeError, "daha yeni"):
                JobStore(database)


if __name__ == "__main__":
    unittest.main()
