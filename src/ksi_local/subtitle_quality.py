"""Mechanical subtitle checks that flag uncertainty without inventing corrections."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from typing import Any

from ksi_local.glossary import Glossary
from ksi_local.subtitles import Cue


@dataclass(frozen=True)
class QualityIssue:
    cue_index: int
    level: str
    code: str
    message: str


@dataclass(frozen=True)
class SubtitleQualityReport:
    source_language: str
    cue_count: int
    passed: bool
    error_count: int
    warning_count: int
    flagged_cue_count: int
    issues: tuple[QualityIssue, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "source_language": self.source_language,
            "cue_count": self.cue_count,
            "passed": self.passed,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "flagged_cue_count": self.flagged_cue_count,
            "issues": [asdict(issue) for issue in self.issues],
        }


def _milliseconds(value: str) -> int:
    hours, minutes, rest = value.split(":")
    seconds, millis = rest.split(",")
    return (
        int(hours) * 3_600_000
        + int(minutes) * 60_000
        + int(seconds) * 1000
        + int(millis)
    )


def _plain(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def assess_subtitle_quality(
    source_cues: list[Cue],
    translated_cues: list[Cue],
    *,
    source_language: str,
    glossary: Glossary,
) -> SubtitleQualityReport:
    """Check structure, readability, suspicious copies and requested terminology."""
    issues: list[QualityIssue] = []
    if len(source_cues) != len(translated_cues):
        issues.append(
            QualityIssue(
                0,
                "error",
                "cue_count_mismatch",
                "Kaynak ve Türkçe altyazı satırı sayısı eşleşmiyor.",
            )
        )
    previous_end = 0
    for position, (source, target) in enumerate(
        zip(source_cues, translated_cues, strict=False), start=1
    ):
        # The SRT writer renumbers cleaned cues consecutively, so reports use the
        # final visible row number rather than a possibly sparse source index.
        cue_index = position
        if (source.start, source.end) != (target.start, target.end):
            issues.append(
                QualityIssue(
                    cue_index,
                    "error",
                    "timestamp_mismatch",
                    "Türkçe satırın zaman kodu kaynakla eşleşmiyor.",
                )
            )
        start = _milliseconds(target.start)
        end = _milliseconds(target.end)
        if end <= start:
            issues.append(
                QualityIssue(cue_index, "error", "invalid_duration", "Süre geçersiz.")
            )
            continue
        if start < previous_end:
            issues.append(
                QualityIssue(
                    cue_index,
                    "warning",
                    "overlap",
                    "Altyazı önceki satırla zaman bakımından çakışıyor.",
                )
            )
        previous_end = max(previous_end, end)
        source_text = _plain(source.text)
        target_text = _plain(target.text)
        if not target_text:
            issues.append(
                QualityIssue(cue_index, "error", "empty", "Türkçe metin boş.")
            )
            continue
        seconds = (end - start) / 1000
        characters_per_second = len(target_text) / seconds
        if characters_per_second > 25:
            issues.append(
                QualityIssue(
                    cue_index,
                    "warning",
                    "fast_reading",
                    f"Okuma hızı yüksek: {characters_per_second:.1f} karakter/sn.",
                )
            )
        lines = [line.strip() for line in target.text.splitlines() if line.strip()]
        if len(lines) > 2 or any(len(line) > 48 for line in lines):
            issues.append(
                QualityIssue(
                    cue_index,
                    "warning",
                    "long_line",
                    "Satır iki satırı veya satır başına 48 karakteri aşıyor.",
                )
            )
        if len(source_text) >= 10:
            similarity = SequenceMatcher(
                None, source_text.casefold(), target_text.casefold()
            ).ratio()
            if similarity >= 0.92:
                issues.append(
                    QualityIssue(
                        cue_index,
                        "warning",
                        "possibly_untranslated",
                        "Kaynak ve Türkçe metin neredeyse aynı; elle kontrol edin.",
                    )
                )
            ratio = len(target_text) / max(len(source_text), 1)
            if ratio < 0.28 or ratio > 3.5:
                issues.append(
                    QualityIssue(
                        cue_index,
                        "warning",
                        "length_ratio",
                        "Çeviri uzunluğu kaynağa göre sıra dışı.",
                    )
                )
        source_folded = source_text.casefold()
        target_folded = target_text.casefold()
        for source_term, target_term in glossary.terms:
            if (
                source_term.casefold() in source_folded
                and target_term.casefold() not in target_folded
            ):
                issues.append(
                    QualityIssue(
                        cue_index,
                        "warning",
                        "glossary_term",
                        f"Sözlük karşılığı görünmüyor: {source_term} → {target_term}.",
                    )
                )
    errors = sum(issue.level == "error" for issue in issues)
    warnings = sum(issue.level == "warning" for issue in issues)
    return SubtitleQualityReport(
        source_language=source_language,
        cue_count=len(translated_cues),
        passed=errors == 0,
        error_count=errors,
        warning_count=warnings,
        flagged_cue_count=len({issue.cue_index for issue in issues}),
        issues=tuple(issues),
    )
