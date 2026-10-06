from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ksi_local.subtitles import (
    Cue,
    clean_rolling_captions,
    parse_srt_text,
    read_srt,
    timestamped_transcript,
    transcript_text,
    write_srt,
)


class SubtitleTests(unittest.TestCase):
    def test_parse_and_write_preserve_timing_and_text(self) -> None:
        content = (
            "\ufeff1\r\n00:00:01,000 --> 00:00:02,500\r\nHello\r\nworld\r\n\r\n"
            "2\r\n00:00:03,000 --> 00:00:04,000 position:50%\r\nAgain\r\n"
        )
        cues = parse_srt_text(content)
        self.assertEqual(cues[0].text, "Hello\nworld")
        self.assertEqual(cues[1].settings, " position:50%")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out.srt"
            write_srt(output, cues)
            self.assertEqual(read_srt(output), cues)

    def test_transcript_removes_markup_and_adjacent_duplicates(self) -> None:
        cues = [
            Cue(1, "00:00:00,000", "00:00:01,000", "<i>Hello</i>"),
            Cue(2, "00:00:01,000", "00:00:02,000", "Hello"),
            Cue(3, "00:00:02,000", "00:00:03,000", "World"),
        ]
        self.assertEqual(transcript_text(cues), "Hello\nWorld")

    def test_invalid_srt_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_srt_text("not an srt")

    def test_timestamped_transcript_adds_traceable_regular_markers(self) -> None:
        cues = [
            Cue(1, "00:00:01,000", "00:00:02,000", "First"),
            Cue(2, "00:00:10,000", "00:00:11,000", "Second"),
            Cue(3, "00:00:35,000", "00:00:36,000", "Third"),
        ]
        result = timestamped_transcript(cues, interval_seconds=30)
        self.assertIn("[00:00:01] First", result)
        self.assertIn("[00:00:35] Third", result)

    def test_clean_rolling_captions_removes_tiny_and_overlapping_lines(self) -> None:
        cues = [
            Cue(1, "00:00:00,000", "00:00:02,000", "first"),
            Cue(2, "00:00:02,000", "00:00:02,010", "first"),
            Cue(3, "00:00:02,010", "00:00:04,000", "first\nsecond"),
            Cue(4, "00:00:04,000", "00:00:04,010", "second"),
            Cue(5, "00:00:04,010", "00:00:06,000", "second\nthird"),
        ]
        cleaned = clean_rolling_captions(cues)
        self.assertEqual([cue.text for cue in cleaned], ["first", "second", "third"])


if __name__ == "__main__":
    unittest.main()
