from __future__ import annotations

import unittest

from ksi_local.downloader import (
    Platform,
    SourceURLValidationError,
    build_download_plan,
    build_probe_plan,
    summarize_probe,
    validate_source_url,
)


class ValidateSourceURLTests(unittest.TestCase):
    def test_accepts_supported_https_hosts(self) -> None:
        cases = {
            "https://youtu.be/abc": Platform.YOUTUBE,
            "https://www.youtube.com/watch?v=abc": Platform.YOUTUBE,
            "https://x.com/example/status/1": Platform.X,
            "https://mobile.twitter.com/example/status/1": Platform.X,
            "https://www.udemy.com/course/example/learn/lecture/1": Platform.UDEMY,
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(validate_source_url(url).platform, expected)

    def test_rejects_http_credentials_ports_and_unlisted_hosts(self) -> None:
        invalid = (
            "http://youtube.com/watch?v=abc",
            "https://user:pass@youtube.com/watch?v=abc",
            "https://youtube.com:8443/watch?v=abc",
            "https://youtube.com.example.org/watch?v=abc",
            "file:///tmp/video.mp4",
            "https://example.com/video",
        )
        for url in invalid:
            with self.subTest(url=url):
                with self.assertRaises(SourceURLValidationError):
                    validate_source_url(url)

    def test_fragment_is_removed_but_query_is_kept(self) -> None:
        source = validate_source_url("https://youtu.be/abc?t=12#fragment")
        self.assertEqual(source.url, "https://youtu.be/abc?t=12")


class ProbePlanTests(unittest.TestCase):
    def test_security_flags_are_always_present(self) -> None:
        plan = build_probe_plan("https://youtu.be/abc", yt_dlp_path="/tools/yt-dlp")
        self.assertEqual(plan.argv[0], "/tools/yt-dlp")
        for required in (
            "--ignore-config",
            "--no-plugin-dirs",
            "--no-remote-components",
            "--skip-download",
            "--dump-single-json",
            "--no-playlist",
        ):
            self.assertIn(required, plan.argv)
        self.assertEqual(plan.argv[-2], "--")

    def test_x_probe_can_report_multiple_media_but_is_capped(self) -> None:
        plan = build_probe_plan("https://x.com/example/status/1")
        self.assertIn("--yes-playlist", plan.argv)
        self.assertIn("--playlist-end", plan.argv)
        self.assertNotIn("--no-playlist", plan.argv)

    def test_only_known_js_runtimes_are_accepted(self) -> None:
        with self.assertRaises(ValueError):
            build_probe_plan(
                "https://youtu.be/abc",
                js_runtime=("unknown", "/tmp/runtime"),
            )

    def test_display_data_does_not_echo_url_query(self) -> None:
        plan = build_probe_plan("https://youtu.be/abc?secret=value")
        serialized = str(plan.display_dict())
        self.assertNotIn("secret", serialized)
        self.assertNotIn("value", serialized)

    def test_download_plan_is_bounded_and_has_no_authentication(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            plan = build_download_plan(
                "https://youtu.be/abc",
                output_directory=directory,
                yt_dlp_path="/tools/yt-dlp",
                ffmpeg_path="/tools/ffmpeg",
            )
        format_selector = plan.argv[plan.argv.index("--format") + 1]
        self.assertIn("bv*[height<=1080][vcodec^=avc1]", format_selector)
        self.assertIn("ba[acodec^=mp4a]", format_selector)
        self.assertIn("--no-playlist", plan.argv)
        self.assertNotIn("--cookies-from-browser", plan.argv)
        subtitle_languages = plan.argv[plan.argv.index("--sub-langs") + 1]
        for code in ("en", "ru", "es", "de", "zh", "fr", "it", "tr"):
            self.assertIn(f"{code}.*", subtitle_languages)
        self.assertEqual(plan.argv[-2], "--")


class ProbeSummaryTests(unittest.TestCase):
    def test_keeps_only_normalized_fields(self) -> None:
        summary = summarize_probe(
            {
                "id": "abc",
                "title": "Example",
                "channel": "Channel",
                "duration": 12.5,
                "formats": [{"height": 1080}, {"height": 720}, {"height": 1080}],
                "subtitles": {"en": [{}], "ru": [{}]},
                "automatic_captions": {"tr": [{}]},
                "webpage_url": "https://example.invalid/?secret=value",
                "http_headers": {"Authorization": "secret"},
            }
        )
        self.assertEqual(summary["available_heights"], [720, 1080])
        self.assertEqual(summary["subtitle_languages"], ["en", "ru"])
        serialized = str(summary)
        self.assertNotIn("webpage_url", serialized)
        self.assertNotIn("Authorization", serialized)
        self.assertNotIn("secret", serialized)


if __name__ == "__main__":
    unittest.main()
