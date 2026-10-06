from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from ksi_local.downloader import (
    DownloaderError,
    FailureCategory,
    SourceURLValidationError,
    build_download_plan,
    build_probe_plan,
    run_download,
)
from ksi_local.providers import (
    ProviderOperation,
    RequestRateLimiter,
    capabilities_for_url,
    tested_public_providers,
)


class Phase27Tests(unittest.TestCase):
    def test_public_list_contains_only_tested_platforms(self) -> None:
        self.assertEqual(tested_public_providers(), ("YouTube", "X", "Udemy (Deneysel)"))
        capabilities = capabilities_for_url("https://youtu.be/example")
        self.assertIn(ProviderOperation.INSPECT, capabilities.operations)
        self.assertIn(ProviderOperation.RESUME, capabilities.operations)
        self.assertFalse(capabilities.drm_supported)

    def test_contract_reuses_strict_ssrf_url_boundary(self) -> None:
        for url in ("http://127.0.0.1/video", "https://localhost/video", "file:///tmp/a"):
            with self.subTest(url=url), self.assertRaises(SourceURLValidationError):
                capabilities_for_url(url)

    def test_rate_limiter_waits_only_for_remaining_interval(self) -> None:
        values = iter((10.0, 11.0, 15.0))
        waits: list[float] = []
        limiter = RequestRateLimiter(5.0, clock=lambda: next(values), wait=waits.append)
        limiter.acquire()
        limiter.acquire()
        self.assertEqual(waits, [4.0])

    def test_output_name_is_fixed_and_cannot_be_injected_by_url(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            plan = build_download_plan(
                "https://youtu.be/example?title=../../secret",
                output_directory=directory,
                yt_dlp_path="/tools/yt-dlp",
            )
            template = plan.argv[plan.argv.index("--output") + 1]
            self.assertEqual(Path(template).parent, Path(directory).resolve())
            self.assertEqual(Path(template).name, "source.%(ext)s")
            self.assertNotIn("secret", str(plan.display_dict()))

    def test_timeouts_are_bounded_and_partial_files_are_not_results(self) -> None:
        import subprocess

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source.mp4.part").write_bytes(b"partial")
            (root / "source.mp4.partial").write_bytes(b"partial")
            (root / "source.mp4.ytdl").write_bytes(b"checkpoint")
            (root / "unrelated.txt").write_text("private")
            (root / "source.mp4").write_bytes(b"complete")
            plan = build_download_plan(
                "https://youtu.be/example",
                output_directory=root,
                yt_dlp_path="/tools/yt-dlp",
            )
            completed = subprocess.CompletedProcess(plan.argv, 0, "", "")
            with patch("ksi_local.downloader.subprocess.run", return_value=completed) as runner:
                self.assertEqual(
                    run_download(plan, timeout_seconds=7),
                    [str((root / "source.mp4").resolve())],
                )
            self.assertEqual(runner.call_args.kwargs["timeout"], 7)

    def test_platform_change_is_sanitized_and_not_retried_as_transient(self) -> None:
        import subprocess

        plan = build_probe_plan("https://youtu.be/example?token=private")
        completed = subprocess.CompletedProcess(plan.argv, 1, "", "ERROR: Unsupported URL https://youtu.be/example?token=private")
        with patch("ksi_local.downloader.subprocess.run", return_value=completed):
            with self.assertRaises(DownloaderError) as caught:
                from ksi_local.downloader import run_probe
                run_probe(plan)
        self.assertEqual(caught.exception.category, FailureCategory.UNSUPPORTED)
        self.assertNotIn("private", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
