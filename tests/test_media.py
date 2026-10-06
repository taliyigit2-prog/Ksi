from __future__ import annotations

import unittest

from ksi_local.media import MAX_DURATION_SECONDS, summarize_ffprobe


class MediaSummaryTests(unittest.TestCase):
    def test_summarizes_audio_video_streams(self) -> None:
        summary = summarize_ffprobe(
            {
                "format": {
                    "duration": "10.250000",
                    "size": "12345",
                    "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
                },
                "streams": [
                    {
                        "codec_type": "video",
                        "codec_name": "h264",
                        "width": 1280,
                        "height": 720,
                    },
                    {"codec_type": "audio", "codec_name": "aac", "channels": 2},
                ],
            }
        )
        self.assertEqual(summary["duration_seconds"], 10.25)
        self.assertEqual(summary["video_stream_count"], 1)
        self.assertEqual(summary["audio_stream_count"], 1)
        self.assertEqual(summary["dimensions"], [[1280, 720]])
        self.assertTrue(summary["within_mvp_duration"])

    def test_marks_over_three_hours_outside_mvp(self) -> None:
        summary = summarize_ffprobe(
            {"format": {"duration": str(MAX_DURATION_SECONDS + 1)}, "streams": []}
        )
        self.assertFalse(summary["within_mvp_duration"])

    def test_handles_invalid_numeric_metadata(self) -> None:
        summary = summarize_ffprobe(
            {"format": {"duration": "unknown", "size": "unknown"}, "streams": []}
        )
        self.assertIsNone(summary["duration_seconds"])
        self.assertIsNone(summary["size_bytes"])
        self.assertFalse(summary["within_mvp_duration"])


if __name__ == "__main__":
    unittest.main()
