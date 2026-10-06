from __future__ import annotations

import hashlib
import tempfile
import unittest
import wave
from pathlib import Path

from ksi_local.creative_lab import (
    LabFeature,
    LabPolicy,
    build_audio_edit_plan,
    build_video_plan,
    candidate_report,
    create_character,
    generate_emoji_svg,
    generate_instrumental,
    generate_pattern,
    sanitize_svg,
)


class Phase34Tests(unittest.TestCase):
    def test_all_pilots_are_independently_disabled_by_default(self) -> None:
        policy = LabPolicy()
        self.assertTrue(all(not enabled for enabled in policy.status().values()))
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(PermissionError):
            generate_pattern(Path(directory) / "no.png", seed=1, policy=policy)

    def test_model_candidates_have_evidence_and_none_is_enabled(self) -> None:
        report = candidate_report()
        self.assertGreaterEqual(len(report), 3)
        self.assertTrue(all(item["license"] and item["apple_silicon_path"] for item in report))
        self.assertTrue(all(not item["enabled"] for item in report))
        self.assertTrue(all(not item["memory_verified"] for item in report))

    def test_seeded_classical_image_is_deterministic_and_never_overwrites(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = LabPolicy({LabFeature.IMAGE})
            first = generate_pattern(root / "one.png", seed=42, policy=policy)
            second = generate_pattern(root / "two.png", seed=42, policy=policy)
            self.assertEqual(hashlib.sha256(first.read_bytes()).digest(), hashlib.sha256(second.read_bytes()).digest())
            with self.assertRaises(FileExistsError):
                generate_pattern(first, seed=42, policy=policy)
            dangling_target = root / "outside.png"
            dangling_output = root / "linked.png"
            dangling_output.symlink_to(dangling_target)
            with self.assertRaises(FileExistsError):
                generate_pattern(dangling_output, seed=42, policy=policy)
            self.assertFalse(dangling_target.exists())

    def test_fictional_character_persists_identity_seed_and_rejects_real_person(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = LabPolicy({LabFeature.CHARACTER})
            card, portrait = create_character(root / "hero", name="Mira", seed=17, traits=["mavi saç", "gezgin"], policy=policy)
            self.assertIn('"fictional": true', card.read_text())
            self.assertIn('"seed": 17', card.read_text())
            self.assertTrue(portrait.is_file())
            with self.assertRaises(PermissionError):
                create_character(root / "person", name="Bir kişi", seed=1, traits=["fotoğraf"], policy=policy, real_person_reference=True)

    def test_instrumental_is_bounded_local_wave(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = generate_instrumental(Path(directory) / "tone.wav", seed=4, duration_seconds=0.5, policy=LabPolicy({LabFeature.MUSIC}))
            with wave.open(str(output), "rb") as handle:
                self.assertEqual(handle.getnchannels(), 1)
                self.assertEqual(handle.getnframes(), 8000)
            with self.assertRaises(ValueError):
                generate_instrumental(Path(directory) / "long.wav", seed=4, duration_seconds=31, policy=LabPolicy({LabFeature.MUSIC}))

    def test_video_and_audio_are_bounded_argv_plans_without_shell(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ffmpeg, image, audio = root / "ffmpeg", root / "image.png", root / "audio.wav"
            ffmpeg.write_bytes(b"tool")
            image.write_bytes(b"image")
            audio.write_bytes(b"audio")
            video = build_video_plan(image, root / "out.mp4", duration_seconds=3, ffmpeg=ffmpeg, policy=LabPolicy({LabFeature.VIDEO}))
            edit = build_audio_edit_plan(audio, root / "out.wav", start=0, duration=2, gain_db=-3, ffmpeg=ffmpeg, policy=LabPolicy({LabFeature.AUDIO}))
            self.assertIsInstance(video, tuple)
            self.assertNotIn("sh", video)
            self.assertIn("volume=-3.00dB", edit[edit.index("-af") + 1])

    def test_svg_rejects_active_external_content_and_emits_safe_emoji(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = LabPolicy({LabFeature.SVG})
            for unsafe in (
                '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
                '<svg xmlns="http://www.w3.org/2000/svg"><image href="https://evil.invalid/a"/></svg>',
                '<!DOCTYPE svg [<!ENTITY x "x">]><svg xmlns="http://www.w3.org/2000/svg"/>',
            ):
                with self.assertRaises(ValueError):
                    sanitize_svg(unsafe, root / f"bad-{len(list(root.iterdir()))}.svg", policy=policy)
            output = generate_emoji_svg(root / "emoji.svg", seed=9, mood="calm", policy=policy)
            self.assertIn("<svg", output.read_text())
            self.assertNotIn("script", output.read_text().casefold())


if __name__ == "__main__":
    unittest.main()
