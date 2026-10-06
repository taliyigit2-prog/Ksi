"""Target-language policy, quality gates and backward-compatible artifact names."""

from __future__ import annotations

import re
from dataclasses import dataclass


# WMT24++ language/locale set referenced by the official TranslateGemma model card.
# Presence in this matrix is a model-design claim, not a KSI quality claim.
TRANSLATEGEMMA_CANDIDATE_LOCALES = (
    "ar-EG", "ar-SA", "bn-IN", "pt-BR", "bg-BG", "fr-CA", "ca-ES",
    "zh-CN", "zh-TW", "hr-HR", "cs-CZ", "da-DK", "nl-NL", "et-EE",
    "pt-PT", "fil-PH", "fi-FI", "fr-FR", "de-DE", "el-GR", "gu-IN",
    "he-IL", "hi-IN", "hu-HU", "is-IS", "id-ID", "it-IT", "ja-JP",
    "kn-IN", "ko-KR", "lv-LV", "lt-LT", "ml-IN", "mr-IN", "es-MX",
    "no-NO", "fa-IR", "pl-PL", "pa-IN", "ro-RO", "ru-RU", "sr-RS",
    "sk-SK", "sl-SI", "sw-KE", "sw-TZ", "sv-SE", "ta-IN", "te-IN",
    "th-TH", "tr-TR", "uk-UA", "ur-PK", "vi-VN", "zu-ZA",
)

VERIFIED_TARGET_LANGUAGES = {
    "tr": "Türkçe", "en": "İngilizce", "ru": "Rusça", "es": "İspanyolca",
    "de": "Almanca", "fr": "Fransızca", "it": "İtalyanca", "zh": "Basitleştirilmiş Çince",
}
TARGET_LANGUAGE_STATUS = {
    "tr": "verified",
    "en": "experimental", "ru": "experimental", "es": "experimental",
    "de": "experimental", "fr": "experimental", "it": "experimental",
    "zh": "experimental",
}


@dataclass(frozen=True)
class TranslationPilot:
    language: str
    sample_count: int
    protected_value_score: float
    terminology_score: float
    user_quality_score: float | None

    @property
    def selectable(self) -> bool:
        return (
            self.language in VERIFIED_TARGET_LANGUAGES
            and self.sample_count >= 3
            and self.protected_value_score == 1.0
            and self.terminology_score >= 0.95
            and self.user_quality_score is not None
            and self.user_quality_score >= 4.0
        )


def normalize_target_language(value: str) -> str:
    code = value.strip().casefold().replace("_", "-").split("-", 1)[0]
    if code not in VERIFIED_TARGET_LANGUAGES:
        raise ValueError("Hedef dil henüz doğrulanmış KSI kalite kapısından geçmedi.")
    return code


def translation_artifact_names(target_language: str, *, document: bool = False) -> tuple[str, ...]:
    code = normalize_target_language(target_language)
    if document:
        modern = tuple(f"belge.{code}{suffix}" for suffix in (".jsonl", ".txt", ".md", ".docx", ".pdf", ".kalite.json"))
        legacy = tuple(f"belge-turkce{suffix}" for suffix in (".jsonl", ".txt", ".md", ".docx", ".pdf", ".kalite.json")) if code == "tr" else ()
    else:
        modern = (f"altyazi.{code}.srt", f"altyazi.{code}.kalite.json", f"dublaj.{code}.mp4")
        legacy = ("turkce.srt", "turkce.kalite.json", "turkce-dublaj.mp4") if code == "tr" else ()
    return (*modern, *legacy)


def protected_values(text: str) -> tuple[str, ...]:
    pattern = (
        r"https?://[^\s<>]+|\b\d+(?:[.,]\d+)?%?\b|"
        r"\b[A-ZÇĞİÖŞÜ]{2,}\b|\b[A-ZÇĞİÖŞÜ][a-zçğıöşü]+[A-ZÇĞİÖŞÜ][\w.-]*\b"
    )
    return tuple(re.findall(pattern, text))


def protected_values_preserved(source: str, translated: str) -> bool:
    return all(translated.count(value) >= source.count(value) for value in protected_values(source))
