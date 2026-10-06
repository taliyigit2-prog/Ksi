"""Small, offline, on-demand source-language detection."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ksi_local.languages import TURKISH_SOURCE_LANGUAGE_NAMES


@dataclass(frozen=True)
class LanguageDetection:
    code: str
    name: str
    confidence: float
    margin: float


_LINGUA_TO_CODE = {
    "ENGLISH": "en",
    "RUSSIAN": "ru",
    "SPANISH": "es",
    "GERMAN": "de",
    "CHINESE": "zh",
    "FRENCH": "fr",
    "ITALIAN": "it",
}

_DOCUMENT_LINGUA_TO_CODE = {
    **_LINGUA_TO_CODE,
    "TURKISH": "tr",
    "PORTUGUESE": "pt",
    "JAPANESE": "ja",
    "KOREAN": "ko",
    "ARABIC": "ar",
    "DUTCH": "nl",
    "POLISH": "pl",
    "UKRAINIAN": "uk",
    "CZECH": "cs",
    "SWEDISH": "sv",
    "GREEK": "el",
}

_DOCUMENT_LANGUAGE_NAMES = {
    **TURKISH_SOURCE_LANGUAGE_NAMES,
    "tr": "Türkçe",
    "pt": "Portekizce",
    "ja": "Japonca",
    "ko": "Korece",
    "ar": "Arapça",
    "nl": "Hollandaca",
    "pl": "Lehçe",
    "uk": "Ukraynaca",
    "cs": "Çekçe",
    "sv": "İsveççe",
    "el": "Yunanca",
}


def _readable_sample(text: str, *, maximum_characters: int = 50_000) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", without_tags).strip()[:maximum_characters]


def _detect_language(
    text: str,
    mapping: dict[str, str],
    names: dict[str, str],
    *,
    minimum_letters: int,
    minimum_confidence: float,
    minimum_margin: float,
) -> LanguageDetection:
    sample = _readable_sample(text)
    letter_count = sum(character.isalpha() for character in sample)
    if letter_count < minimum_letters:
        raise RuntimeError(
            "Dil algılama için yeterli konuşma metni yok. Kaynak dili elle seçin."
        )
    try:
        from lingua import Language, LanguageDetectorBuilder
    except ImportError as error:
        raise RuntimeError("Çevrimdışı dil algılama bileşeni kurulu değil.") from error

    languages = [getattr(Language, name) for name in mapping]
    detector = LanguageDetectorBuilder.from_languages(*languages).build()
    values = detector.compute_language_confidence_values(sample)
    if not values:
        raise RuntimeError("Kaynak dil algılanamadı. Kaynak dili elle seçin.")

    best = values[0]
    runner_up = values[1].value if len(values) > 1 else 0.0
    confidence = float(best.value)
    margin = confidence - float(runner_up)
    code = mapping.get(best.language.name)
    if (
        code is None
        or confidence < minimum_confidence
        or margin < minimum_margin
    ):
        raise RuntimeError("Kaynak dil güvenle algılanamadı. Kaynak dili elle seçin.")
    return LanguageDetection(
        code=code,
        name=names.get(code, code),
        confidence=confidence,
        margin=margin,
    )


def detect_text_language(text: str) -> LanguageDetection:
    """Detect one of KSI Local Studio's seven video source languages offline."""
    try:
        return _detect_language(
            text,
            _LINGUA_TO_CODE,
            TURKISH_SOURCE_LANGUAGE_NAMES,
            minimum_letters=20,
            minimum_confidence=0.55,
            minimum_margin=0.12,
        )
    except RuntimeError as error:
        raise RuntimeError(
            "Kaynak dil güvenle algılanamadı. İngilizce, Rusça, İspanyolca, Almanca, "
            "Çince, Fransızca veya İtalyanca seçeneklerinden birini elle seçin."
        ) from error


def detect_document_language(text: str) -> LanguageDetection:
    """Detect Turkish, the seven stable languages, and ten gated pilot languages."""
    return _detect_language(
        text,
        _DOCUMENT_LINGUA_TO_CODE,
        _DOCUMENT_LANGUAGE_NAMES,
        minimum_letters=20,
        minimum_confidence=0.50,
        minimum_margin=0.08,
    )
