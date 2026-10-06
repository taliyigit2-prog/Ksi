from __future__ import annotations

import unittest

from ksi_local.language_detection import detect_text_language


class LanguageDetectionTests(unittest.TestCase):
    def test_detects_every_supported_language(self) -> None:
        samples = {
            "en": "This detailed educational video explains computers and programming clearly.",
            "ru": (
                "Это подробное обучающее видео о компьютерах и программировании."
            ),
            "es": "Este es un vídeo educativo detallado sobre computadoras y programación.",
            "de": "Dieses Lernvideo erklärt ausführlich Computer und Programmierung.",
            "zh": "这是一个详细介绍计算机和编程知识的教育视频。",
            "fr": (
                "Cette vidéo éducative explique clairement les ordinateurs et la programmation."
            ),
            "it": "Questo video educativo spiega chiaramente i computer e la programmazione.",
        }
        for expected, text in samples.items():
            with self.subTest(expected=expected):
                self.assertEqual(detect_text_language(text).code, expected)

    def test_rejects_too_short_ambiguous_text(self) -> None:
        with self.assertRaises(RuntimeError):
            detect_text_language("Hello")


if __name__ == "__main__":
    unittest.main()
