"""Source-language definitions shared by the GUI and processing workers."""

from __future__ import annotations


SUPPORTED_SOURCE_LANGUAGES = {
    "en": "English",
    "ru": "Russian",
    "es": "Spanish",
    "de": "German",
    "zh": "Chinese",
    "fr": "French",
    "it": "Italian",
}

TURKISH_SOURCE_LANGUAGE_NAMES = {
    "en": "İngilizce",
    "ru": "Rusça",
    "es": "İspanyolca",
    "de": "Almanca",
    "zh": "Çince",
    "fr": "Fransızca",
    "it": "İtalyanca",
}

AUTO_LANGUAGE = "auto"
SOURCE_LANGUAGE_CHOICES = (AUTO_LANGUAGE, *SUPPORTED_SOURCE_LANGUAGES)


def turkish_language_name(code: str) -> str:
    """Return a user-facing Turkish name while keeping unknown codes readable."""
    if code == AUTO_LANGUAGE:
        return "Dili otomatik algıla"
    return TURKISH_SOURCE_LANGUAGE_NAMES.get(code, code)
