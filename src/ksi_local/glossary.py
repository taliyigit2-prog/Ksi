"""Validated, small terminology lists shared by ASR and translation workers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ksi_local.languages import SUPPORTED_SOURCE_LANGUAGES


MAX_GLOSSARY_BYTES = 1024 * 1024
MAX_TERMS_PER_LANGUAGE = 200


@dataclass(frozen=True)
class Glossary:
    source_language: str
    terms: tuple[tuple[str, str], ...]
    preserve: tuple[str, ...]

    def prompt_fragment(self) -> str:
        if not self.terms and not self.preserve:
            return ""
        lines = [
            "Apply this terminology when the corresponding source term occurs; "
            "do not force it elsewhere:"
        ]
        lines.extend(f"- {source} => {target}" for source, target in self.terms)
        if self.preserve:
            lines.append("Preserve these names exactly: " + ", ".join(self.preserve))
        return "\n".join(lines)

    def source_prompt(self) -> str:
        values = [source for source, _target in self.terms] + list(self.preserve)
        return ", ".join(values)[:500]


def empty_glossary(source_language: str) -> Glossary:
    return Glossary(source_language, (), ())


def load_glossary(path: str | Path | None, source_language: str) -> Glossary:
    if source_language not in SUPPORTED_SOURCE_LANGUAGES:
        raise ValueError("Terim sözlüğü için kaynak dili desteklenmiyor.")
    if path is None:
        return empty_glossary(source_language)
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError("Terim sözlüğü dosyası bulunamadı.")
    if source.stat().st_size > MAX_GLOSSARY_BYTES:
        raise ValueError("Terim sözlüğü 1 MiB sınırını aşıyor.")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError("Terim sözlüğü geçerli JSON değil.") from error
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("Terim sözlüğü şema sürümü desteklenmiyor.")
    all_terms = payload.get("terms")
    language_terms = (
        all_terms.get(source_language, {}) if isinstance(all_terms, dict) else {}
    )
    if not isinstance(language_terms, dict):
        raise ValueError("Kaynak dile ait terimler nesne biçiminde olmalıdır.")
    if len(language_terms) > MAX_TERMS_PER_LANGUAGE:
        raise ValueError("Bir dil için en çok 200 terim kullanılabilir.")
    terms: list[tuple[str, str]] = []
    for raw_source, raw_target in language_terms.items():
        source_term = str(raw_source).strip()
        target_term = str(raw_target).strip()
        if (
            not source_term
            or not target_term
            or len(source_term) > 100
            or len(target_term) > 100
        ):
            raise ValueError("Terim sözlüğünde boş veya çok uzun bir terim var.")
        terms.append((source_term, target_term))
    raw_preserve = payload.get("preserve", [])
    if not isinstance(raw_preserve, list) or len(raw_preserve) > 100:
        raise ValueError("Korunacak adlar listesi geçersiz.")
    preserve = tuple(
        value
        for item in raw_preserve
        if (value := str(item).strip()) and len(value) <= 100
    )
    return Glossary(source_language, tuple(terms), preserve)
