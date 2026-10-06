from __future__ import annotations

import json
import unittest

from ksi_local.subtitles import Cue
from ksi_local.summarization import split_text, summarize_transcript
from ksi_local.translation import translate_cues


class FakeClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    def generate(self, **kwargs: object) -> str:
        self.calls.append(kwargs)
        return self.responses.pop(0)


class TranslationTests(unittest.TestCase):
    def test_translation_preserves_timestamps(self) -> None:
        cues = [
            Cue(7, "00:00:01,000", "00:00:02,000", "Hello"),
            Cue(9, "00:00:02,000", "00:00:03,000", "World"),
        ]
        response = json.dumps(
            {"translations": [{"id": 7, "text": "Merhaba"}, {"id": 9, "text": "Dünya"}]}
        )
        translated = translate_cues(cues, source_language="en", client=FakeClient([response]))
        self.assertEqual([cue.text for cue in translated], ["Merhaba", "Dünya"])
        self.assertEqual(translated[0].start, cues[0].start)

    def test_rejects_missing_translation(self) -> None:
        cues = [Cue(1, "00:00:01,000", "00:00:02,000", "Hello")]
        with self.assertRaises(RuntimeError):
            translate_cues(
                cues,
                source_language="en",
                client=FakeClient(['{"translations":[]}']),
            )

    def test_missing_batch_items_fall_back_to_smaller_batches(self) -> None:
        cues = [
            Cue(1, "00:00:01,000", "00:00:02,000", "One"),
            Cue(2, "00:00:02,000", "00:00:03,000", "Two"),
        ]
        client = FakeClient(
            [
                '{"translations":[{"id":1,"text":"Bir"}]}',
                '{"translations":[{"id":1,"text":"Bir"}]}',
                '{"translations":[{"id":2,"text":"İki"}]}',
            ]
        )
        translated = translate_cues(cues, source_language="en", client=client)
        self.assertEqual([cue.text for cue in translated], ["Bir", "İki"])
        self.assertEqual(len(client.calls), 3)

    def test_model_stays_loaded_between_batches_then_unloads(self) -> None:
        cues = [
            Cue(1, "00:00:01,000", "00:00:02,000", "One"),
            Cue(2, "00:00:02,000", "00:00:03,000", "Two"),
        ]
        client = FakeClient(
            [
                '{"translations":[{"id":1,"text":"Bir"}]}',
                '{"translations":[{"id":2,"text":"İki"}]}',
            ]
        )
        translate_cues(cues, source_language="en", client=client, batch_size=1)
        self.assertEqual(client.calls[0]["keep_alive"], "5m")
        self.assertEqual(client.calls[1]["keep_alive"], 0)

    def test_spanish_translation_direction_is_in_prompt(self) -> None:
        cue = Cue(1, "00:00:01,000", "00:00:02,000", "Hola mundo")
        client = FakeClient(['{"translations":[{"id":1,"text":"Merhaba dünya"}]}'])
        translate_cues([cue], source_language="es", client=client)
        self.assertIn("Spanish to natural Turkish", str(client.calls[0]["prompt"]))


class SummarizationTests(unittest.TestCase):
    def test_split_and_hierarchical_summary(self) -> None:
        text = "\n".join(["x" * 700, "y" * 700, "z" * 700])
        chunks = split_text(text, max_chars=1000)
        self.assertEqual(len(chunks), 3)
        client = FakeClient(["bir", "iki", "üç", "son özet"])
        result = summarize_transcript(text, client=client, max_chars=1000)
        self.assertEqual(result, "son özet")
        self.assertEqual(len(client.calls), 4)


if __name__ == "__main__":
    unittest.main()
