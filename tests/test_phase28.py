from __future__ import annotations

import unittest
import json
import tempfile
from pathlib import Path
from ksi_local.cli import build_parser

from ksi_local.ollama_client import OllamaClient
from ksi_local.multilingual_document import translate_document_target
from ksi_local.subtitles import Cue
from ksi_local.translation import translate_cues
from ksi_local.translation_targets import (
    TARGET_LANGUAGE_STATUS,
    TRANSLATEGEMMA_CANDIDATE_LOCALES,
    TranslationPilot,
    protected_values_preserved,
    translation_artifact_names,
)


class _Client(OllamaClient):
    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[str] = []

    def generate(self, **kwargs: object) -> str:
        self.prompts.append(str(kwargs["prompt"]))
        return self.response


class Phase28Tests(unittest.TestCase):
    def test_official_candidate_matrix_is_not_a_quality_claim(self) -> None:
        self.assertEqual(len(TRANSLATEGEMMA_CANDIDATE_LOCALES), 55)
        experimental = TranslationPilot("ja", 3, 1.0, 1.0, None)
        self.assertFalse(experimental.selectable)
        self.assertEqual(TARGET_LANGUAGE_STATUS["tr"], "verified")
        self.assertTrue(all(
            status == "experimental"
            for language, status in TARGET_LANGUAGE_STATUS.items()
            if language != "tr"
        ))

    def test_turkish_can_translate_to_verified_target_and_preserve_values(self) -> None:
        cue = Cue(1, "00:00:00,000", "00:00:02,000", "OpenAI 42% https://example.com")
        client = _Client('{"translations":[{"id":1,"text":"OpenAI 42% https://example.com"}]}')
        result = translate_cues([cue], source_language="tr", target_language="de", client=client)
        self.assertEqual(result[0].text, cue.text)
        self.assertIn("target_lang_code=de", client.prompts[0])

    def test_same_language_skips_model_and_unverified_target_is_blocked(self) -> None:
        cue = Cue(1, "00:00:00,000", "00:00:01,000", "Merhaba")
        client = _Client("should not run")
        self.assertEqual(translate_cues([cue], source_language="tr", target_language="tr", client=client), [cue])
        self.assertEqual(client.prompts, [])
        with self.assertRaises(ValueError):
            translate_cues([cue], source_language="tr", target_language="ja", client=client)

    def test_output_schema_is_language_coded_and_turkish_is_backward_compatible(self) -> None:
        names = translation_artifact_names("tr")
        self.assertIn("altyazi.tr.srt", names)
        self.assertIn("turkce.srt", names)
        self.assertEqual(translation_artifact_names("de")[0], "altyazi.de.srt")
        self.assertTrue(protected_values_preserved("OpenAI 25 https://x.test", "OpenAI 25 https://x.test"))
        self.assertFalse(protected_values_preserved("OpenAI 25", "OpenAI"))

    def test_cli_exposes_target_language_without_changing_legacy_default(self) -> None:
        parser = build_parser()
        common = ["translate-srt", "in.srt", "out.srt", "--source-language", "tr", "--ollama", "/bin/ollama", "--models-directory", "/models"]
        self.assertEqual(parser.parse_args(common).target_language, "tr")
        self.assertEqual(parser.parse_args([*common, "--target-language", "fr"]).target_language, "fr")

    def test_document_outputs_use_target_language_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical = root / "canonical.jsonl"
            canonical.write_text(json.dumps({
                "schema_version": 1, "id": "B000001", "sequence": 1,
                "source_sha256": "a" * 64, "source_format": "md",
                "block_type": "paragraph", "text": "OpenAI 42%",
                "location": {"line_start": 1, "line_end": 1},
                "extraction_method": "test", "detected_language": "tr",
                "language_confidence": 1.0, "ocr_confidence": None,
                "style": {}, "flags": [],
            }, ensure_ascii=False) + "\n")
            client = _Client('{"translations":[{"id":1,"text":"OpenAI 42%"}]}')
            result = translate_document_target(
                canonical, root / "outputs", source_language="tr",
                target_language="de", client=client,
            )
            self.assertEqual(result.jsonl_path.name, "belge.de.jsonl")
            self.assertTrue(result.pdf_path.is_file())
            self.assertFalse((root / "outputs/belge-turkce.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
