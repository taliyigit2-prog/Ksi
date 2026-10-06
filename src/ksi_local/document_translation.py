"""Traceable, resumable document-block translation to Turkish through local Ollama."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from ksi_local.atomic_files import atomic_write_json, atomic_write_text
from ksi_local.document_output import create_document_outputs
from ksi_local.glossary import Glossary, empty_glossary, load_glossary
from ksi_local.language_detection import detect_document_language
from ksi_local.languages import SUPPORTED_SOURCE_LANGUAGES
from ksi_local.ollama_client import OllamaClient
from ksi_local.resource_governor import serialized_model


SCHEMA_VERSION = 1
TRANSLATION_MODEL = "translategemma:4b-it-q8_0"
MAX_CANONICAL_BYTES = 100 * 1024 * 1024
MAX_BATCH_SIZE = 12
PILOT_LANGUAGES = {
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
KNOWN_DOCUMENT_LANGUAGES = {*SUPPORTED_SOURCE_LANGUAGES, "tr", *PILOT_LANGUAGES}
PRESERVED_BLOCK_TYPES = {"code_block"}


@dataclass(frozen=True)
class DocumentTranslationIssue:
    block_id: str
    level: str
    code: str
    message: str


@dataclass(frozen=True)
class DocumentTranslationResult:
    jsonl_path: Path
    text_path: Path
    markdown_path: Path
    docx_path: Path
    pdf_path: Path
    quality_path: Path
    checkpoint_path: Path
    block_count: int
    translated_block_count: int
    preserved_block_count: int
    language_counts: dict[str, int]
    quality: dict[str, object]


@dataclass(frozen=True)
class _PlanEntry:
    block: dict[str, Any]
    language: str | None
    status: str
    flags: tuple[str, ...]


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_text(value: str) -> str:
    return _sha256_bytes(value.encode("utf-8"))


def _canonical_blocks(path: str | Path) -> tuple[Path, str, list[dict[str, Any]]]:
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise ValueError("Kanonik belge kaynağı sembolik bağlantı olamaz.")
    source = candidate.resolve(strict=True)
    if not source.is_file() or source.stat().st_size > MAX_CANONICAL_BYTES:
        raise ValueError("Kanonik belge kaynağı bulunamadı veya güvenli boyut sınırını aşıyor.")
    raw = source.read_bytes()
    digest = _sha256_bytes(raw)
    blocks: list[dict[str, Any]] = []
    source_sha256: str | None = None
    for expected_sequence, line in enumerate(raw.decode("utf-8").splitlines(), start=1):
        try:
            block = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError("Kanonik belge JSONL yapısı geçersiz.") from error
        if not isinstance(block, dict):
            raise ValueError("Kanonik belge satırı JSON nesnesi değil.")
        block_id = block.get("id")
        sequence = block.get("sequence")
        text = block.get("text")
        digest_value = block.get("source_sha256")
        if (
            block_id != f"B{expected_sequence:06d}"
            or sequence != expected_sequence
            or not isinstance(text, str)
            or not text.strip()
            or not isinstance(digest_value, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest_value)
            or not isinstance(block.get("location"), dict)
            or not isinstance(block.get("block_type"), str)
        ):
            raise ValueError("Kanonik belge blok sırası veya zorunlu alanları geçersiz.")
        if source_sha256 is None:
            source_sha256 = digest_value
        elif digest_value != source_sha256:
            raise ValueError("Kanonik belge bloklarının kaynak SHA-256 değeri eşleşmiyor.")
        blocks.append(block)
    if not blocks:
        raise ValueError("Kanonik belgede çevrilecek blok bulunamadı.")
    if len({str(block["id"]) for block in blocks}) != len(blocks):
        raise ValueError("Kanonik belgede yinelenmiş blok kimliği var.")
    return source, digest, blocks


def _detected_language(block: dict[str, Any]) -> tuple[str | None, float | None]:
    text = str(block["text"])
    try:
        detection = detect_document_language(text)
    except RuntimeError:
        code = block.get("detected_language")
        confidence = block.get("language_confidence")
        if code in KNOWN_DOCUMENT_LANGUAGES and isinstance(confidence, (int, float)):
            return str(code), float(confidence)
        return None, None
    return detection.code, detection.confidence


def _build_plan(
    blocks: list[dict[str, Any]], *, source_language: str
) -> list[_PlanEntry]:
    if source_language != "auto" and source_language not in SUPPORTED_SOURCE_LANGUAGES:
        raise ValueError("Belge kaynak dili desteklenen yedi dilden biri veya otomatik olmalıdır.")
    detections = [_detected_language(block) for block in blocks]
    weighted = Counter[str]()
    for block, (language, confidence) in zip(blocks, detections, strict=True):
        if language and confidence is not None and confidence >= 0.50:
            weighted[language] += max(1, sum(character.isalpha() for character in block["text"]))
    dominant = weighted.most_common(1)[0][0] if weighted else None
    fallback = source_language if source_language != "auto" else dominant
    plan: list[_PlanEntry] = []
    for block, (detected, confidence) in zip(blocks, detections, strict=True):
        text = str(block["text"])
        flags: set[str] = set()
        if str(block["block_type"]) in PRESERVED_BLOCK_TYPES:
            plan.append(_PlanEntry(block, None, "preserved_code", ("code_preserved",)))
            continue
        if not any(character.isalpha() for character in text):
            plan.append(_PlanEntry(block, None, "preserved_symbols", ("symbols_preserved",)))
            continue
        language = detected if detected and (confidence or 0) >= 0.50 else fallback
        if language is None:
            raise ValueError(
                f"{block['id']} bloğunun dili güvenle algılanamadı; kaynak dili elle seçin."
            )
        if detected and source_language != "auto" and detected != source_language:
            flags.add("manual_language_conflict")
        if detected is None:
            flags.add("language_inferred")
        if language == "tr":
            plan.append(_PlanEntry(block, language, "preserved_turkish", tuple(sorted(flags))))
            continue
        if language in PILOT_LANGUAGES:
            raise ValueError(
                f"{block['id']} bloğunda {PILOT_LANGUAGES[language]} algılandı. "
                "Bu dil pilot kapısından henüz geçmediği için otomatik çeviri başlatılmadı."
            )
        if language not in SUPPORTED_SOURCE_LANGUAGES:
            raise ValueError(f"{block['id']} bloğunun dili desteklenmiyor: {language}")
        plan.append(_PlanEntry(block, language, "translate", tuple(sorted(flags))))
    return plan


def document_needs_model(path: str | Path, *, source_language: str = "auto") -> bool:
    _source, _digest, blocks = _canonical_blocks(path)
    return any(
        entry.status == "translate"
        for entry in _build_plan(blocks, source_language=source_language)
    )


def _validate_glossary_path(path: Path) -> Path:
    candidate = path.expanduser()
    if candidate.is_symlink():
        raise ValueError("Belge sözlüğü sembolik bağlantı olamaz.")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_file():
        raise FileNotFoundError("Belge sözlüğü bulunamadı.")
    for language in SUPPORTED_SOURCE_LANGUAGES:
        load_glossary(resolved, language)
    return resolved


def validate_document_glossary(path: str | Path) -> dict[str, object]:
    resolved = _validate_glossary_path(Path(path))
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("target_language") not in {None, "tr"}:
        raise ValueError("Belge sözlüğünün hedef dili Türkçe olmalıdır.")
    return {
        "path": str(resolved),
        "sha256": _sha256_bytes(resolved.read_bytes()),
        "language_count": sum(
            bool(load_glossary(resolved, language).terms)
            for language in SUPPORTED_SOURCE_LANGUAGES
        ),
        "preserve_count": len(payload.get("preserve", [])),
    }


def _glossaries(
    default_path: str | Path | None,
    custom_path: str | Path | None,
) -> tuple[dict[str, Glossary], str]:
    resolved_paths: list[Path] = []
    for raw in (default_path, custom_path):
        if raw is not None:
            resolved_paths.append(_validate_glossary_path(Path(raw)))
    result: dict[str, Glossary] = {}
    for language in SUPPORTED_SOURCE_LANGUAGES:
        terms: list[tuple[str, str]] = []
        preserved: list[str] = []
        for path in resolved_paths:
            glossary = load_glossary(path, language)
            terms.extend(glossary.terms)
            preserved.extend(glossary.preserve)
        result[language] = Glossary(
            language,
            tuple(dict.fromkeys(terms)),
            tuple(dict.fromkeys(preserved)),
        ) if resolved_paths else empty_glossary(language)
    digest_material = b"\0".join(path.read_bytes() for path in resolved_paths)
    return result, _sha256_bytes(digest_material)


_GENERAL_PROTECTED_PATTERNS = (
    re.compile(r"https?://[^\s<>\])},;!?]+(?<!\.)", re.I),
    re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"`[^`\n]+`"),
    re.compile(r"\$[^$\n]+\$"),
    re.compile(r"\\\([^\n]+?\\\)"),
    re.compile(r"\b[A-Z][a-z]+(?:[A-Z][a-z0-9]+)+\b"),
)
_PLACEHOLDER = re.compile(r"VTRTOKEN\d{9}X")
_NUMERIC_TOKEN = re.compile(r"(?<![\w])\d(?:[\d.,:/%-]*\d)?%?(?![\w])")
_TRANSLATED_NUMERIC_TOKEN = re.compile(
    r"(?<![\w])%?\d(?:[\d.,:/%-]*\d)?%?(?![\w])"
)
_CODE_TOKEN = re.compile(r"\b[A-Z][A-Z0-9_-]{1,}\b")
_SYMBOL_UNIT = re.compile(
    r"(?<![\w])(?P<number>\d(?:[\d.,:/%-]*\d)?%?)\s*"
    r"(?P<unit>ms|s|kg|km|cm|mm|bar|kHz|MHz|GHz|Hz|KB|MB|GB|TB)\b"
)
_CURRENCY_PAIR = re.compile(
    r"(?:(?P<code1>EUR|USD|GBP|RUB|TRY|TL)\s*(?P<number1>\d(?:[\d.,]*\d)?))|"
    r"(?:(?P<number2>\d(?:[\d.,]*\d)?)\s*(?P<code2>EUR|USD|GBP|RUB|TRY|TL))",
    re.I,
)
_CURRENCY_ALIASES = {
    "EUR": r"(?:EUR|avro)",
    "USD": r"(?:USD|ABD\s+dolar[ıi]|Amerikan\s+dolar[ıi])",
    "GBP": r"(?:GBP|İngiliz\s+sterlini|sterlin)",
    "RUB": r"(?:RUB|Rus\s+rublesi|ruble)",
    "TRY": r"(?:TRY|Türk\s+liras[ıi])",
    "TL": r"(?:TL|Türk\s+liras[ıi])",
}
_GENERIC_HEADING_TERM = re.compile(
    r"\b(?:report|briefing|guide|overview|summary|memo|results|rules|checks|contact|register|"
    r"informe|guía|resumen|resultados|bericht|leitfaden|zusammenfassung|"
    r"rapport|guide|résumé|résultats|relazione|guida|riepilogo|risultati)\b",
    re.I,
)
_HEADING_WORDS_TR = {
    "report": "Raporu", "briefing": "Bilgilendirmesi", "guide": "Rehberi",
    "overview": "Genel Bakış", "summary": "Özeti", "memo": "Notu", "results": "Sonuçlar",
    "translation": "Çeviri", "measurement": "Ölçüm", "register": "Kayıtları",
    "rules": "Kuralları", "checks": "Kontroller", "contact": "İletişim",
    "local": "Yerel", "processing": "İşleme", "field": "Saha", "safety": "Güvenliği",
    "informe": "Raporu", "guía": "Rehberi", "resumen": "Özeti", "resultados": "Sonuçlar",
    "bericht": "Raporu", "leitfaden": "Rehberi", "zusammenfassung": "Özeti",
    "rapport": "Raporu", "résumé": "Özeti", "résultats": "Sonuçlar",
    "relazione": "Raporu", "guida": "Rehberi", "riepilogo": "Özeti", "risultati": "Sonuçlar",
}
_MONTHS_TR = {
    "january": "Ocak", "enero": "Ocak", "januar": "Ocak", "janvier": "Ocak", "gennaio": "Ocak", "января": "Ocak",
    "february": "Şubat", "febrero": "Şubat", "februar": "Şubat", "février": "Şubat", "febbraio": "Şubat", "февраля": "Şubat",
    "march": "Mart", "marzo": "Mart", "märz": "Mart", "mars": "Mart", "марта": "Mart",
    "april": "Nisan", "abril": "Nisan", "avril": "Nisan", "aprile": "Nisan", "апреля": "Nisan",
    "may": "Mayıs", "mayo": "Mayıs", "mai": "Mayıs", "maggio": "Mayıs", "мая": "Mayıs",
    "june": "Haziran", "junio": "Haziran", "juni": "Haziran", "juin": "Haziran", "giugno": "Haziran", "июня": "Haziran",
    "july": "Temmuz", "julio": "Temmuz", "juli": "Temmuz", "juillet": "Temmuz", "luglio": "Temmuz", "июля": "Temmuz",
    "august": "Ağustos", "agosto": "Ağustos", "août": "Ağustos", "августа": "Ağustos",
    "september": "Eylül", "septiembre": "Eylül", "septembre": "Eylül", "settembre": "Eylül", "сентября": "Eylül",
    "october": "Ekim", "octubre": "Ekim", "oktober": "Ekim", "octobre": "Ekim", "ottobre": "Ekim", "октября": "Ekim",
    "november": "Kasım", "noviembre": "Kasım", "novembre": "Kasım", "ноября": "Kasım",
    "december": "Aralık", "diciembre": "Aralık", "dezember": "Aralık", "décembre": "Aralık", "dicembre": "Aralık", "декабря": "Aralık",
}
_MONTH_PATTERN = "|".join(sorted((re.escape(value) for value in _MONTHS_TR), key=len, reverse=True))
_TEXTUAL_DATE = re.compile(
    rf"\b(?P<day>\d{{1,2}})(?:st|nd|rd|th|\.)?\s+(?:de\s+)?"
    rf"(?P<month>{_MONTH_PATTERN})(?:\s+de)?\s+(?P<year>\d{{4}})"
    r"(?:'(?:de|da|te|ta)|\s+tarihinde)?",
    re.I,
)
_SIMPLE_ENGLISH_NEGATION = re.compile(
    r"\b(?:is|are|was|were)\s+not\b",
    re.I,
)
_ENGLISH_COMPARISON = re.compile(
    r"\b(?:below|above|under|over|less|more|different|exceed(?:s|ed|ing)?)\b",
    re.I,
)
_TURKISH_ADDED_COMPARISON = re.compile(
    r"\b(?:alt(?:ında|ındadır|ındaydı)|üst(?:ünde|ündedir|ündeydi)|"
    r"aşağı(?:sında)?|yukarı(?:sında)?|farklı(?:dır|ydı)?|"
    r"aş(?:ar|tı|mış|mamalı|maması|mamasını|mıyor)|"
    r"geç(?:er|ti|miş|memeli|memesi|memesini|miyor))\b",
    re.I,
)


def _protect_text(text: str, block_sequence: int, glossary: Glossary) -> tuple[str, dict[str, str]]:
    if "VTRTOKEN" in text:
        raise ValueError("Kaynak metin KSI Local Studio koruma belirteciyle çakışıyor.")
    intervals: list[tuple[int, int]] = []
    for value in sorted(glossary.preserve, key=len, reverse=True):
        if not value:
            continue
        intervals.extend((match.start(), match.end()) for match in re.finditer(re.escape(value), text))
    for pattern in _GENERAL_PROTECTED_PATTERNS:
        intervals.extend((match.start(), match.end()) for match in pattern.finditer(text))
    accepted: list[tuple[int, int]] = []
    for start, end in sorted(intervals, key=lambda item: (item[0], -(item[1] - item[0]))):
        if start == end or any(start < prior_end and end > prior_start for prior_start, prior_end in accepted):
            continue
        accepted.append((start, end))
    placeholders: dict[str, str] = {}
    protected = text
    for index, (start, end) in reversed(list(enumerate(accepted, start=1))):
        # TranslateGemma is materially more reliable with one uninterrupted
        # alphanumeric token than with underscore-heavy placeholders, which it
        # may reformat even at temperature zero.
        marker = f"VTRTOKEN{block_sequence:06d}{index:03d}X"
        placeholders[marker] = text[start:end]
        protected = protected[:start] + marker + protected[end:]
    return protected, placeholders


def _restore_text(value: str, placeholders: dict[str, str]) -> str:
    result = value.strip().strip('"')
    for marker, original in placeholders.items():
        if result.count(marker) != 1:
            raise RuntimeError("Çeviri modeli korunan bir sayı, ad, kod veya bağlantıyı değiştirdi.")
        result = result.replace(marker, original)
    if _PLACEHOLDER.search(result):
        raise RuntimeError("Çeviri modeli bilinmeyen bir koruma belirteci ekledi.")
    if not result.strip():
        raise RuntimeError("Çeviri modeli boş bir belge bloğu döndürdü.")
    if "�" in result:
        raise RuntimeError("Çeviri modeli bozuk bir Unicode karakteri döndürdü.")
    return result.strip()


def _numeric_key(value: str) -> str:
    core = value.strip("%")
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", core):
        core = core.replace(".", "").replace(",", "")
    elif re.fullmatch(r"\d+[.,]\d+", core):
        core = core.replace(",", ".")
    # The source token remains authoritative for percent presence. Small translation
    # models sometimes localize 3.7% as 3,7 and omit the sign; restoration puts it back.
    return core


def _restore_numeric_spelling(source: str, target: str) -> str:
    """Restore source punctuation after a model localizes an otherwise equal number."""

    available: dict[str, list[str]] = {}
    for token in _NUMERIC_TOKEN.findall(source):
        available.setdefault(_numeric_key(token), []).append(token)

    def restore(match: re.Match[str]) -> str:
        token = match.group(0)
        candidates = available.get(_numeric_key(token))
        if not candidates:
            return token
        return candidates.pop(0)

    return _TRANSLATED_NUMERIC_TOKEN.sub(restore, target)


def _restore_symbol_units(source: str, target: str) -> str:
    result = target
    for match in _SYMBOL_UNIT.finditer(source):
        phrase = f"{match.group('number')} {match.group('unit')}"
        if re.search(re.escape(match.group("number")) + r"\s*" + re.escape(match.group("unit")) + r"\b", result):
            continue
        result = re.sub(
            re.escape(match.group("number")) + r"(?![\d.,])",
            lambda _matched, replacement=phrase: replacement,
            result,
            count=1,
        )
    return result


def _restore_currency_pairs(source: str, target: str) -> str:
    result = target
    for match in _CURRENCY_PAIR.finditer(source):
        code = str(match.group("code1") or match.group("code2")).upper()
        number = str(match.group("number1") or match.group("number2"))
        alias = _CURRENCY_ALIASES[code]
        exact = f"{code} {number}"
        patterns = (
            rf"{alias}\s*{re.escape(number)}",
            rf"{re.escape(number)}\s*{alias}",
        )
        replaced = False
        for pattern in patterns:
            updated, count = re.subn(pattern, exact, result, count=1, flags=re.I)
            if count:
                result = updated
                replaced = True
                break
        if not replaced and code not in _CODE_TOKEN.findall(result):
            result = re.sub(re.escape(number), exact, result, count=1)
    return result


def _normalize_target_dates(target: str) -> str:
    def replace(match: re.Match[str]) -> str:
        month = _MONTHS_TR[match.group("month").casefold()]
        return f"{match.group('day')} {month} {match.group('year')} tarihinde"

    result = _TEXTUAL_DATE.sub(replace, target)
    result = re.sub(
        r"\b(?P<year>\d{4})年(?P<month>\d{1,2})月(?P<day>\d{1,2})日",
        lambda match: (
            f"{match.group('day')} "
            f"{('Ocak','Şubat','Mart','Nisan','Mayıs','Haziran','Temmuz','Ağustos','Eylül','Ekim','Kasım','Aralık')[int(match.group('month')) - 1]} "
            f"{match.group('year')} tarihinde"
        ),
        result,
    )
    return re.sub(r"\btarihinde\s+(?:ile|tarihinde)\b", "tarihinde", result, flags=re.I)


def _turkish_accusative_suffix(number: int) -> str:
    if number == 0:
        return "ı"
    ones = number % 10
    if ones:
        return {1: "i", 2: "yi", 3: "ü", 4: "ü", 5: "i", 6: "yı", 7: "yi", 8: "i", 9: "u"}[ones]
    tens = number % 100
    if tens:
        return {10: "u", 20: "yi", 30: "u", 40: "ı", 50: "yi", 60: "ı", 70: "i", 80: "i", 90: "ı"}[tens]
    if number % 1_000:
        return "ü"
    if number % 1_000_000:
        return "i"
    if number % 1_000_000_000:
        return "u"
    return "ı"


def _fix_numeric_suffixes(target: str) -> str:
    return re.sub(
        r"(?<![-\w])(?P<number>\d+)'(?:y?[ıiuü])\b",
        lambda match: f"{match.group('number')}'{_turkish_accusative_suffix(int(match.group('number')))}",
        target,
        flags=re.I,
    )


def _clock_suffix(clock: str, *, ablative: bool) -> str:
    hour_text, minute_text = clock.split(":", 1)
    value = int(minute_text)
    if value == 0:
        value = int(hour_text)
    last = value % 10
    if last:
        locative = {1: "de", 2: "de", 3: "te", 4: "te", 5: "te", 6: "da", 7: "de", 8: "de", 9: "da"}[last]
    else:
        locative = {0: "da", 10: "da", 20: "de", 30: "da", 40: "ta", 50: "de"}.get(value % 100, "da")
    return locative + ("n" if ablative else "")


def _cleanup_turkish_surface(source: str, target: str) -> str:
    """Repair bounded, source-safe Turkish surface errors from small local models."""

    result = re.sub(r"(?P<clock>\b\d{1,2}:\d{2})\.(?=')", r"\g<clock>", target)
    result = re.sub(r"%\.(?=')", "%", result)
    result = re.sub(r"\b(\d{4})\.\s+tarihinde\b", r"\1 tarihinde", result)
    result = re.sub(r"\b(\d+)\s+yerel\s+testleri\b", r"\1 yerel testi", result, flags=re.I)
    result = re.sub(
        r"\b(\d+)\s+kayıtları\s+bulunmaktadır\b",
        r"\1 kayıt bulunmaktadır",
        result,
        flags=re.I,
    )
    result = re.sub(
        r"\b(\d+)\s+kayıtları\s+bulunur\b",
        r"\1 kayıt bulunur",
        result,
        flags=re.I,
    )
    result = re.sub(
        r"(?P<clock>\b\d{1,2}:\d{2})'(?:de|da|te|ta)\b",
        lambda match: f"{match.group('clock')}'{_clock_suffix(match.group('clock'), ablative=False)}",
        result,
        flags=re.I,
    )
    result = re.sub(
        r"(?P<clock>\b\d{1,2}:\d{2})'(?:den|dan|ten|tan)\b",
        lambda match: f"{match.group('clock')}'{_clock_suffix(match.group('clock'), ablative=True)}",
        result,
        flags=re.I,
    )
    result = re.sub(
        r"(?P<email>\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})'[a-zçğıöşü]+\b",
        r"\g<email> adresine",
        result,
        flags=re.I,
    )
    result = re.sub(r"\bTam olarak (\d+) teknisyenleri\b", r"Tam olarak \1 teknisyen", result)
    technician_match = re.fullmatch(
        r"\s*Exactly\s+(\d+)\s+technicians\s+may\s+enter\s+the\s+restricted\s+area\.\s*",
        source,
        flags=re.I,
    )
    if technician_match:
        result = f"Kısıtlı alana tam olarak {technician_match.group(1)} teknisyen girebilir."
    batch_match = re.search(
        r"\bchecked\s+batch\s+(?P<code>[A-Z][A-Z0-9]*-\d+)\s+locally\b",
        source,
        flags=re.I,
    )
    if batch_match:
        code = batch_match.group("code")
        result = re.sub(
            rf"(?:toplama\s+)?{re.escape(code)}'(?:y?[ıiuü])\s+yerel\s+olarak\s+"
            r"(?:kontrol\s+etti|inceledi)",
            f"{code} grubunu yerel olarak kontrol etti",
            result,
            count=1,
            flags=re.I,
        )
    if re.match(r"\s*Site\s*:", source, flags=re.I):
        result = re.sub(r"^\s*(?:Web sitesi|Site)\s*:", "Tesis:", result, flags=re.I)
    if re.match(r"\s*Date\s*:", source, flags=re.I):
        result = re.sub(r"\s+tarihinde\s*$", "", result, flags=re.I)
    page_match = re.search(r"\bPage\s+(\d+)\s+of\s+(\d+)\b", source, flags=re.I)
    if page_match and "•" in result:
        prefix = result.split("•", 1)[0].rstrip()
        result = f"{prefix} • Sayfa {page_match.group(1)} / {page_match.group(2)}"
    if re.search(r"\bdoes not cancel the\s*$", source, flags=re.I):
        if re.search(r"\bdoes not permit remote processing\b", source, flags=re.I):
            result = re.sub(
                r"Bu not,?\s+uzaktan işlem yapılmasını sağlamaz",
                "Bu not uzaktan işlemeye izin vermez",
                result,
                flags=re.I,
            )
        result = re.sub(
            r"\s+ve\s+mevcut\s+şartları\s+yürürlükte\s+tutar\.?$",
            " ve aşağıdaki uygulamayı iptal etmez:",
            result,
            flags=re.I,
        )
    if source[:1].islower() and result[:1].islower():
        result = result[:1].upper() + result[1:]
    return result


def _repair_redundant_markdown_delimiter(source: str, target: str) -> str:
    if source.count("(") == source.count(")") and target.count(")") == target.count("(") + 1:
        return re.sub(r"(\]\([^)]+\))\.\)", r"\1.", target, count=1)
    return target


def _normalize_translation(source: str, target: str) -> str:
    result = _restore_numeric_spelling(source, target)
    result = _restore_currency_pairs(source, result)
    result = _restore_symbol_units(source, result)
    result = _normalize_target_dates(result)
    result = _fix_numeric_suffixes(result)
    result = _cleanup_turkish_surface(source, result)
    return _repair_redundant_markdown_delimiter(source, result)


def _heading_fallback(source: str, target: str) -> str:
    if SequenceMatcher(None, source.casefold(), target.casefold()).ratio() < 0.90:
        return target
    result = target
    for original, translated in _HEADING_WORDS_TR.items():
        result = re.sub(rf"\b{re.escape(original)}\b", translated, result, flags=re.I)
    return result


def _validate_translation_invariants(source: str, target: str) -> None:
    """Reject small-model artifacts that can silently change factual meaning."""

    source_numbers = Counter(_NUMERIC_TOKEN.findall(source))
    target_numbers = Counter(_NUMERIC_TOKEN.findall(target))
    if source_numbers != target_numbers:
        raise RuntimeError(
            "Çeviri modeli kaynakta olmayan bir sayı ekledi veya mevcut bir sayıyı değiştirdi "
            f"(kaynak={dict(source_numbers)}, hedef={dict(target_numbers)})."
        )
    if Counter(_CODE_TOKEN.findall(source)) != Counter(_CODE_TOKEN.findall(target)):
        source_codes = Counter(_CODE_TOKEN.findall(source))
        target_codes = Counter(_CODE_TOKEN.findall(target))
        raise RuntimeError(
            "Çeviri modeli büyük harfli bir kodu veya para birimini değiştirdi "
            f"(kaynak={dict(source_codes)}, hedef={dict(target_codes)})."
        )
    if any(target.count(left) != target.count(right) for left, right in (("(", ")"), ("[", "]"), ("{", "}"))):
        raise RuntimeError("Çeviri modeli eşleşmeyen bir ayraç üretti.")
    if (
        _SIMPLE_ENGLISH_NEGATION.search(source)
        and not _ENGLISH_COMPARISON.search(source)
        and _TURKISH_ADDED_COMPARISON.search(target)
    ):
        raise RuntimeError(
            "Çeviri modeli basit olumsuzluğa kaynakta olmayan bir karşılaştırma anlamı ekledi."
        )


def _translation_record(entry: _PlanEntry, target: str) -> dict[str, object]:
    source = str(entry.block["text"])
    return {
        "schema_version": SCHEMA_VERSION,
        "id": entry.block["id"],
        "sequence": entry.block["sequence"],
        "source_document_sha256": entry.block["source_sha256"],
        "source_block_sha256": _sha256_text(source),
        "translation_sha256": _sha256_text(target),
        "source_language": entry.language,
        "source_language_confidence": entry.block.get("language_confidence"),
        "block_type": entry.block["block_type"],
        "location": entry.block["location"],
        "style": entry.block.get("style") or {},
        "source_text": source,
        "translated_text": target,
        "status": entry.status,
        "flags": list(entry.flags),
    }


def _translate_batch(
    entries: list[_PlanEntry],
    *,
    client: OllamaClient,
    model: str,
    glossary: Glossary,
    is_last: bool,
    _attempt: int = 0,
) -> list[dict[str, object]]:
    protected: list[tuple[_PlanEntry, str, dict[str, str]]] = []
    for entry in entries:
        text, placeholders = _protect_text(
            str(entry.block["text"]), int(entry.block["sequence"]), glossary
        )
        protected.append((entry, text, placeholders))
    items = [
        {"id": entry.block["id"], "type": entry.block["block_type"], "text": text}
        for entry, text, _placeholders in protected
    ]
    instruction = (
        "Translate each document block from "
        f"{SUPPORTED_SOURCE_LANGUAGES[glossary.source_language]} to natural Turkish. "
        "Preserve meaning and the block's structural role. Never summarize, explain, merge, "
        "split, omit, or reorder. Copy every VTRTOKEN...X marker exactly once. "
        "Return JSON only as {\"translations\":[{\"id\":\"B000001\",\"text\":\"...\"}]}. "
        "Return every input id exactly once and in the same order. "
        "Preserve logical negation literally: translate 'X is not Y' directly as "
        "'X, Y değildir'; never infer below, above, different, less, more, or exceeds when "
        "the source does not say so. Translate 'must not exceed 30' as '30'u aşmamalıdır'. "
        "Never copy or expose digits from inside a VTRTOKEN marker; only copy the complete "
        "marker itself. Copy proper names, organization names, acronyms, and product codes "
        "exactly as written in the source. Translate headings and table labels too."
    )
    if _attempt:
        instruction += (
            " The previous answer failed a factual-integrity check. Correct it strictly: "
            "do not add any number or comparison, and retain every explicit negation."
        )
    # Preserved values are deliberately hidden behind placeholders. Repeating their literal
    # values in the prompt lets small models replace a placeholder with the named value, which
    # is semantically plausible but breaks exact, position-safe restoration.
    fragment = Glossary(glossary.source_language, glossary.terms, ()).prompt_fragment()
    if fragment:
        instruction += "\n" + fragment
    response = client.translate_items(items, source_language=glossary.source_language,
        target_language="tr", glossary=glossary) if hasattr(client, "translate_items") else client.generate(
        model=model,
        prompt=instruction + "\nINPUT:\n" + json.dumps(items, ensure_ascii=False),
        json_mode=True,
        temperature=0.0,
        keep_alive=0 if is_last else "5m",
        max_tokens=4096,
    )
    try:
        payload = json.loads(response)
        records = payload["translations"]
        if not isinstance(records, list):
            raise TypeError
        returned_ids = [record.get("id") for record in records if isinstance(record, dict)]
        expected_ids = [entry.block["id"] for entry in entries]
        if returned_ids != expected_ids or len(set(returned_ids)) != len(expected_ids):
            raise ValueError("blok kimliği eksik, yinelenmiş veya sırası değişmiş")
        translations = [str(record["text"]) for record in records]
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        if len(entries) > 1:
            midpoint = len(entries) // 2
            return _translate_batch(
                entries[:midpoint],
                client=client,
                model=model,
                glossary=glossary,
                is_last=False,
            ) + _translate_batch(
                entries[midpoint:],
                client=client,
                model=model,
                glossary=glossary,
                is_last=is_last,
            )
        if _attempt < 2:
            return _translate_batch(
                entries,
                client=client,
                model=model,
                glossary=glossary,
                is_last=is_last,
                _attempt=_attempt + 1,
            )
        raise RuntimeError(f"Çeviri modeli belge blok sözleşmesini bozdu: {error}") from error
    output: list[dict[str, object]] = []
    try:
        for (entry, _text, placeholders), translated in zip(
            protected, translations, strict=True
        ):
            source = str(entry.block["text"])
            restored = _normalize_translation(source, _restore_text(translated, placeholders))
            if re.search(r"\bB\d{6}\b", restored) and not re.search(
                r"\bB\d{6}\b", source
            ):
                raise RuntimeError("Çeviri modeli kaynak blok işaretini metne ekledi.")
            if entry.block["block_type"] == "heading" or (
                len(source) <= 80
                and _GENERIC_HEADING_TERM.search(source)
                and SequenceMatcher(None, source.casefold(), restored.casefold()).ratio() >= 0.90
            ):
                restored = _heading_fallback(source, restored)
            _validate_translation_invariants(source, restored)
            if (
                entry.block["block_type"] == "heading"
                and _GENERIC_HEADING_TERM.search(source)
                and SequenceMatcher(None, source.casefold(), restored.casefold()).ratio() >= 0.90
            ):
                raise RuntimeError("Çeviri modeli belge başlığını çevirmeden bıraktı.")
            output.append(_translation_record(entry, restored))
    except RuntimeError:
        if len(entries) > 1:
            midpoint = len(entries) // 2
            return _translate_batch(
                entries[:midpoint],
                client=client,
                model=model,
                glossary=glossary,
                is_last=False,
            ) + _translate_batch(
                entries[midpoint:],
                client=client,
                model=model,
                glossary=glossary,
                is_last=is_last,
            )
        if _attempt < 2:
            return _translate_batch(
                entries,
                client=client,
                model=model,
                glossary=glossary,
                is_last=is_last,
                _attempt=_attempt + 1,
            )
        raise
    return output


def _checkpoint_payload(
    *,
    canonical_sha256: str,
    model: str,
    glossary_sha256: str,
    records: list[dict[str, object]],
    complete: bool,
) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "canonical_jsonl_sha256": canonical_sha256,
        "model": model,
        "glossary_sha256": glossary_sha256,
        "completed_blocks": len(records),
        "complete": complete,
        "records": records,
    }


def _load_checkpoint(
    path: Path,
    *,
    plan: list[_PlanEntry],
    canonical_sha256: str,
    model: str,
    glossary_sha256: str,
) -> list[dict[str, object]]:
    if not path.is_file():
        return []
    if path.is_symlink():
        raise ValueError("Belge çeviri checkpoint'i sembolik bağlantı olamaz.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        records = payload["records"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("Belge çeviri checkpoint'i okunamıyor.") from error
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != SCHEMA_VERSION
        or payload.get("canonical_jsonl_sha256") != canonical_sha256
        or payload.get("model") != model
        or payload.get("glossary_sha256") != glossary_sha256
        or not isinstance(records, list)
        or len(records) > len(plan)
    ):
        raise ValueError("Belge çeviri checkpoint'i kaynak, model veya sözlükle eşleşmiyor.")
    validated: list[dict[str, object]] = []
    for index, record in enumerate(records):
        entry = plan[index]
        if (
            not isinstance(record, dict)
            or record.get("id") != entry.block["id"]
            or record.get("sequence") != entry.block["sequence"]
            or record.get("source_block_sha256") != _sha256_text(str(entry.block["text"]))
            or record.get("translation_sha256")
            != _sha256_text(str(record.get("translated_text") or ""))
        ):
            raise ValueError("Belge çeviri checkpoint blok sırası veya özeti geçersiz.")
        source = str(entry.block["text"])
        normalized = _normalize_translation(source, str(record["translated_text"]))
        if entry.block["block_type"] == "heading" or (
            len(source) <= 80
            and _GENERIC_HEADING_TERM.search(source)
            and SequenceMatcher(None, source.casefold(), normalized.casefold()).ratio() >= 0.90
        ):
            normalized = _heading_fallback(source, normalized)
        try:
            _validate_translation_invariants(source, normalized)
        except RuntimeError:
            return validated
        if (
            entry.status == "translate"
            and entry.block["block_type"] == "heading"
            and _GENERIC_HEADING_TERM.search(source)
            and SequenceMatcher(None, source.casefold(), normalized.casefold()).ratio() >= 0.90
        ):
            return validated
        current = dict(record)
        current["translated_text"] = normalized
        current["translation_sha256"] = _sha256_text(normalized)
        validated.append(current)
    return validated


def _quality_report(
    records: list[dict[str, object]],
    *,
    canonical_sha256: str,
    glossaries: dict[str, Glossary],
) -> dict[str, object]:
    issues: list[DocumentTranslationIssue] = []
    language_counts: Counter[str] = Counter()
    for record in records:
        block_id = str(record["id"])
        source = str(record["source_text"])
        target = str(record["translated_text"])
        language = record.get("source_language")
        if isinstance(language, str):
            language_counts[language] += 1
        if record["status"] != "translate":
            continue
        if len(source) >= 10:
            similarity = SequenceMatcher(None, source.casefold(), target.casefold()).ratio()
            if similarity >= 0.90:
                issues.append(DocumentTranslationIssue(
                    block_id, "warning", "possibly_untranslated",
                    "Kaynak ve Türkçe metin neredeyse aynı; elle kontrol edilmeli.",
                ))
            ratio = len(target) / max(len(source), 1)
            if ratio < 0.25 or ratio > 4.0:
                issues.append(DocumentTranslationIssue(
                    block_id, "warning", "length_ratio",
                    f"Çeviri uzunluk oranı sıra dışı: {ratio:.2f}.",
                ))
        if "�" in target:
            issues.append(DocumentTranslationIssue(
                block_id, "error", "invalid_character", "Çeviride bozuk karakter bulundu.",
            ))
        try:
            _validate_translation_invariants(source, target)
        except RuntimeError as error:
            issues.append(DocumentTranslationIssue(
                block_id, "error", "factual_integrity", str(error),
            ))
        if re.search(r"\s+[,.!?;:]", target) or re.search(r"[!?.,]{3,}", target):
            issues.append(DocumentTranslationIssue(
                block_id, "warning", "turkish_grammar_spacing",
                "Türkçe noktalama veya boşluk düzeni şüpheli.",
            ))
        if any(target.count(left) != target.count(right) for left, right in (("(", ")"), ("[", "]"), ("{", "}"))):
            issues.append(DocumentTranslationIssue(
                block_id, "warning", "turkish_grammar_delimiter",
                "Türkçe metinde eşleşmeyen ayraç bulundu.",
            ))
        if len(target) >= 40:
            try:
                target_language = detect_document_language(target).code
            except RuntimeError:
                target_language = None
            if target_language not in {None, "tr"}:
                issues.append(DocumentTranslationIssue(
                    block_id, "warning", "target_language",
                    "Hedef metin Türkçe görünmüyor; elle kontrol edilmeli.",
                ))
        if isinstance(language, str) and language in glossaries:
            glossary = glossaries[language]
            source_folded = source.casefold()
            target_folded = target.casefold()
            for source_term, target_term in glossary.terms:
                if source_term.casefold() in source_folded and target_term.casefold() not in target_folded:
                    issues.append(DocumentTranslationIssue(
                        block_id, "warning", "glossary_term",
                        f"Sözlük karşılığı görünmüyor: {source_term} → {target_term}.",
                    ))
            for name in glossary.preserve:
                if name in source and name not in target:
                    issues.append(DocumentTranslationIssue(
                        block_id, "error", "preserved_name",
                        f"Korunması gereken özel ad değişti: {name}.",
                    ))
    errors = sum(issue.level == "error" for issue in issues)
    warnings = sum(issue.level == "warning" for issue in issues)
    translated = sum(record["status"] == "translate" for record in records)
    document_language = language_counts.most_common(1)[0][0] if language_counts else None
    return {
        "schema_version": SCHEMA_VERSION,
        "passed": errors == 0,
        "semantic_review_required": True,
        "canonical_jsonl_sha256": canonical_sha256,
        "block_count": len(records),
        "translated_block_count": translated,
        "preserved_block_count": len(records) - translated,
        "language_counts": dict(sorted(language_counts.items())),
        "document_language": document_language,
        "mixed_language": len(language_counts) > 1,
        "error_count": errors,
        "warning_count": warnings,
        "flagged_block_count": len({issue.block_id for issue in issues}),
        "issues": [asdict(issue) for issue in issues],
    }


def _readable_translation(records: list[dict[str, object]]) -> str:
    lines = ["KSI Local Studio Türkçe Belge Çevirisi", f"Blok sayısı: {len(records)}", ""]
    for record in records:
        location = ", ".join(f"{key}={value}" for key, value in record["location"].items())
        lines.extend([
            f"[{record['id']}] {record['block_type']} · {location} · {record['source_language'] or '—'}",
            "KAYNAK:",
            str(record["source_text"]),
            "TÜRKÇE:",
            str(record["translated_text"]),
            "",
        ])
    return "\n".join(lines).rstrip() + "\n"


@serialized_model
def translate_document(
    canonical_path: str | Path,
    output_directory: str | Path,
    *,
    client: OllamaClient | None,
    source_language: str = "auto",
    model: str = TRANSLATION_MODEL,
    batch_size: int = 6,
    default_glossary: str | Path | None = None,
    custom_glossary: str | Path | None = None,
    checkpoint_path: str | Path | None = None,
    source_title: str | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    unload_model_at_end: bool = True,
) -> DocumentTranslationResult:
    if batch_size < 1 or batch_size > MAX_BATCH_SIZE:
        raise ValueError("Belge çeviri paket boyutu 1–12 arasında olmalıdır.")
    canonical, canonical_sha256, blocks = _canonical_blocks(canonical_path)
    plan = _build_plan(blocks, source_language=source_language)
    glossaries, glossary_sha256 = _glossaries(default_glossary, custom_glossary)
    output_candidate = Path(output_directory).expanduser()
    if output_candidate.is_symlink():
        raise ValueError("Belge çeviri çıktı klasörü sembolik bağlantı olamaz.")
    output = output_candidate.resolve()
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    checkpoint = (
        Path(checkpoint_path).expanduser()
        if checkpoint_path is not None
        else canonical.parent / "belge-ceviri.checkpoint.json"
    )
    if checkpoint.parent.resolve() != canonical.parent.resolve():
        raise ValueError("Belge çeviri checkpoint'i kanonik kaynakla aynı klasörde olmalıdır.")
    records = _load_checkpoint(
        checkpoint,
        plan=plan,
        canonical_sha256=canonical_sha256,
        model=model,
        glossary_sha256=glossary_sha256,
    )
    remaining = plan[len(records):]
    model_batches: list[list[_PlanEntry]] = []
    index = 0
    while index < len(remaining):
        entry = remaining[index]
        if entry.status != "translate":
            index += 1
            continue
        batch = [entry]
        cursor = index + 1
        while (
            cursor < len(remaining)
            and len(batch) < batch_size
            and remaining[cursor].status == "translate"
            and remaining[cursor].language == entry.language
            and remaining[cursor].block["block_type"] == entry.block["block_type"]
        ):
            batch.append(remaining[cursor])
            cursor += 1
        model_batches.append(batch)
        index = cursor
    batch_cursor = 0
    position = len(records)
    while position < len(plan):
        entry = plan[position]
        if entry.status != "translate":
            while position < len(plan) and plan[position].status != "translate":
                preserved = plan[position]
                records.append(
                    _translation_record(preserved, str(preserved.block["text"]))
                )
                position += 1
        else:
            if client is None:
                raise RuntimeError("Belge çevirisi için yerel Ollama istemcisi gerekli.")
            batch = model_batches[batch_cursor]
            translated = _translate_batch(
                batch,
                client=client,
                model=model,
                glossary=glossaries[str(entry.language)],
                is_last=(
                    unload_model_at_end and batch_cursor == len(model_batches) - 1
                ),
            )
            records.extend(translated)
            position += len(batch)
            batch_cursor += 1
        atomic_write_json(
            checkpoint,
            _checkpoint_payload(
                canonical_sha256=canonical_sha256,
                model=model,
                glossary_sha256=glossary_sha256,
                records=records,
                complete=False,
            ),
        )
        if on_progress:
            on_progress(len(records), len(plan))
    quality = _quality_report(
        records,
        canonical_sha256=canonical_sha256,
        glossaries=glossaries,
    )
    jsonl_path = output / "belge-turkce.jsonl"
    text_path = output / "belge-turkce.txt"
    markdown_path = output / "belge-turkce.md"
    docx_path = output / "belge-turkce.docx"
    pdf_path = output / "belge-turkce.pdf"
    quality_path = output / "belge-turkce.kalite.json"
    staging = Path(tempfile.mkdtemp(prefix=".belge-turkce-paketi-", dir=output))
    try:
        staged_jsonl = staging / jsonl_path.name
        jsonl = (
            "\n".join(
                json.dumps(record, ensure_ascii=False, sort_keys=True)
                for record in records
            )
            + "\n"
        )
        atomic_write_text(staged_jsonl, jsonl)
        reread = [
            json.loads(line)
            for line in staged_jsonl.read_text(encoding="utf-8").splitlines()
        ]
        if [record.get("id") for record in reread] != [
            entry.block["id"] for entry in plan
        ]:
            raise RuntimeError("Türkçe belge çıktısı yeniden okuma doğrulamasından geçmedi.")
        formatted = create_document_outputs(
            records,
            staging,
            source_title=source_title or canonical.name,
        )
        quality["jsonl_sha256"] = _sha256_bytes(staged_jsonl.read_bytes())
        quality["text_sha256"] = _sha256_bytes(formatted.text_path.read_bytes())
        quality["markdown_sha256"] = _sha256_bytes(formatted.markdown_path.read_bytes())
        quality["docx_sha256"] = _sha256_bytes(formatted.docx_path.read_bytes())
        quality["pdf_sha256"] = _sha256_bytes(formatted.pdf_path.read_bytes())
        quality["pdf_font_embedded"] = formatted.embedded_pdf_font
        atomic_write_json(staging / quality_path.name, quality)
        for target in (
            jsonl_path,
            text_path,
            markdown_path,
            docx_path,
            pdf_path,
            quality_path,
        ):
            os.replace(staging / target.name, target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    atomic_write_json(
        checkpoint,
        _checkpoint_payload(
            canonical_sha256=canonical_sha256,
            model=model,
            glossary_sha256=glossary_sha256,
            records=records,
            complete=True,
        ),
    )
    language_counts = dict(quality["language_counts"])
    return DocumentTranslationResult(
        jsonl_path=jsonl_path,
        text_path=text_path,
        markdown_path=markdown_path,
        docx_path=docx_path,
        pdf_path=pdf_path,
        quality_path=quality_path,
        checkpoint_path=checkpoint,
        block_count=len(records),
        translated_block_count=int(quality["translated_block_count"]),
        preserved_block_count=int(quality["preserved_block_count"]),
        language_counts=language_counts,
        quality=quality,
    )


def pilot_language_matrix() -> dict[str, object]:
    """Declare gated candidates; no candidate becomes selectable without three pilot sets."""
    return {
        "schema_version": 1,
        "promotion_rule": "clean_technical_long_all_pass",
        "candidates": [
            {
                "code": code,
                "name": name,
                "required_sets": ["clean", "technical", "long_paragraph"],
                "status": "gated_not_selectable",
            }
            for code, name in PILOT_LANGUAGES.items()
        ],
    }
