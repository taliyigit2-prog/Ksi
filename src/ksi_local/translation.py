"""Supported source languages to Turkish subtitle translation through Ollama."""

from __future__ import annotations

import json
from dataclasses import dataclass

from ksi_local.glossary import Glossary, empty_glossary
from ksi_local.languages import SUPPORTED_SOURCE_LANGUAGES
from ksi_local.ollama_client import OllamaClient
from ksi_local.subtitles import Cue
from ksi_local.translation_targets import (
    VERIFIED_TARGET_LANGUAGES,
    normalize_target_language,
    protected_values_preserved,
)


@dataclass(frozen=True)
class TranslationProgress:
    completed: int
    total: int


def _clean_translation(text: str) -> str:
    return text.strip().strip('"').replace("\\n", "\n")


def translate_cues(
    cues: list[Cue],
    *,
    source_language: str,
    client: OllamaClient,
    model: str = "translategemma:4b-it-q8_0",
    batch_size: int = 12,
    on_progress: object | None = None,
    on_checkpoint: object | None = None,
    glossary: Glossary | None = None,
    unload_on_finish: bool = True,
    target_language: str = "tr",
) -> list[Cue]:
    """Translate cues in bounded batches and preserve every original timestamp."""
    source_names = {**SUPPORTED_SOURCE_LANGUAGES, "tr": "Turkish"}
    if source_language not in source_names:
        raise ValueError("Seçilen kaynak dili KSI Local Studio tarafından desteklenmiyor.")
    if batch_size < 1 or batch_size > 30:
        raise ValueError("Çeviri paket boyutu 1–30 arasında olmalıdır.")
    target = normalize_target_language(target_language)
    if source_language == target:
        return list(cues)
    active_glossary = glossary or empty_glossary(source_language)
    if active_glossary.source_language != source_language:
        raise ValueError("Terim sözlüğünün dili çeviri kaynak diliyle eşleşmiyor.")

    translated: list[Cue] = []
    target_prompt_names = {
        "tr": "Turkish", "en": "English", "ru": "Russian", "es": "Spanish",
        "de": "German", "fr": "French", "it": "Italian", "zh": "Simplified Chinese",
    }
    instruction = (
        "You are a precise professional subtitle translator. Translate from "
        f"{source_names[source_language]} to natural "
        f"{target_prompt_names[target]} "
        f"(source_lang_code={source_language}, target_lang_code={target}). "
        "Preserve meaning, names, technical terms, numbers, and line breaks. "
        "Never summarize or add commentary. Return valid JSON only."
    )
    glossary_prompt = active_glossary.prompt_fragment()
    if glossary_prompt:
        instruction += "\n" + glossary_prompt

    def translate_batch(batch: list[Cue], *, is_last: bool) -> list[Cue]:
        items = [{"id": cue.index, "text": cue.text} for cue in batch]
        prompt = (
            instruction
            + "\nTranslate each item's text. Return exactly this JSON shape: "
            '{"translations":[{"id":1,"text":"..."}]}. '
            "Every input id must occur exactly once.\nINPUT:\n"
            + json.dumps(items, ensure_ascii=False)
        )
        # Gemma instruction-tuned models do not use a separate system role, so
        # all constraints deliberately live in the user turn.
        response = client.generate(
            model=model,
            prompt=prompt,
            json_mode=True,
            keep_alive=0 if is_last and unload_on_finish else "5m",
        )
        try:
            payload = json.loads(response)
            records = payload["translations"]
            mapping = {
                int(record["id"]): _clean_translation(str(record["text"]))
                for record in records
                if isinstance(record, dict)
            }
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            failure = RuntimeError(f"Çeviri modeli geçersiz paket yanıtı verdi: {error}")
            if len(batch) == 1:
                raise failure from error
            midpoint = len(batch) // 2
            return translate_batch(batch[:midpoint], is_last=False) + translate_batch(
                batch[midpoint:], is_last=is_last
            )
        expected = {cue.index for cue in batch}
        if set(mapping) != expected or any(not value for value in mapping.values()):
            if len(batch) == 1:
                raise RuntimeError("Çeviri modeli bir altyazı satırını çeviremedi.")
            midpoint = len(batch) // 2
            return translate_batch(batch[:midpoint], is_last=False) + translate_batch(
                batch[midpoint:], is_last=is_last
            )
        if any(
            not protected_values_preserved(cue.text, mapping[cue.index])
            for cue in batch
        ):
            if len(batch) == 1:
                raise RuntimeError("Çeviri sayı, ad veya URL koruma kapısından geçmedi.")
            midpoint = len(batch) // 2
            return translate_batch(batch[:midpoint], is_last=False) + translate_batch(
                batch[midpoint:], is_last=is_last
            )
        return [cue.with_text(mapping[cue.index]) for cue in batch]

    for offset in range(0, len(cues), batch_size):
        batch = cues[offset : offset + batch_size]
        is_last = offset + batch_size >= len(cues)
        translated.extend(translate_batch(batch, is_last=is_last))
        if callable(on_checkpoint):
            on_checkpoint(list(translated))
        if callable(on_progress):
            on_progress(TranslationProgress(len(translated), len(cues)))
    return translated
