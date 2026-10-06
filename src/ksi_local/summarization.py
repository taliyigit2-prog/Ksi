"""Hierarchical Turkish transcript summarization for small local models."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from typing import Any

from ksi_local.ollama_client import OllamaClient
from ksi_local.subtitles import Cue
from ksi_local.resource_governor import serialized_model


ALLOWED_CATEGORIES = {"ana_konu", "önemli_fikir", "sonuç", "eylem"}
FINAL_SECTIONS = (
    "short_summary",
    "main_topics",
    "important_ideas",
    "conclusions",
    "actions",
)


def _claim_output_schema(source_ids: list[str]) -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "claims": {
                "type": "array",
                "minItems": 1,
                "maxItems": 6,
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "maxLength": 180},
                        "category": {
                            "type": "string",
                            "enum": sorted(ALLOWED_CATEGORIES),
                        },
                        "source_ids": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 12,
                            "uniqueItems": True,
                            "items": {"type": "string", "enum": source_ids},
                        },
                    },
                    "required": ["text", "category", "source_ids"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["claims"],
        "additionalProperties": False,
    }


def _final_output_schema(claim_ids: list[str]) -> dict[str, object]:
    limits = {
        "short_summary": 3,
        "main_topics": 6,
        "important_ideas": 6,
        "conclusions": 4,
        "actions": 4,
    }
    properties: dict[str, object] = {}
    for section, limit in limits.items():
        field: dict[str, object] = {
            "type": "array",
            "maxItems": limit,
            "uniqueItems": True,
            "items": {"type": "string", "enum": claim_ids},
        }
        if section in {"short_summary", "main_topics"}:
            field["minItems"] = 1
        properties[section] = field
    return {
        "type": "object",
        "properties": properties,
        "required": list(FINAL_SECTIONS),
        "additionalProperties": False,
    }


@dataclass(frozen=True)
class SourceItem:
    id: str
    timestamp: str
    text: str


@dataclass(frozen=True)
class EvidenceClaim:
    id: str
    text: str
    category: str
    source_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class SummaryProgress:
    completed: int
    total: int


@dataclass(frozen=True)
class SummaryResult:
    markdown: str
    traceability: dict[str, object]
    quality: dict[str, object]
    claims: tuple[EvidenceClaim, ...]
    chunk_count: int


def _seconds(timestamp: str) -> int:
    hours, minutes, seconds = timestamp[:8].split(":")
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds)


def source_items_from_cues(cues: list[Cue]) -> list[SourceItem]:
    """Create stable source identifiers so summary statements can cite exact cues."""
    items: list[SourceItem] = []
    previous = ""
    for cue in cues:
        text = re.sub(r"<[^>]+>", " ", cue.text)
        text = re.sub(r"\s+", " ", text).strip()
        if not text or text == previous:
            continue
        items.append(SourceItem(f"S{len(items) + 1:06d}", cue.start[:8], text))
        previous = text
    if not items:
        raise ValueError("Özetlenecek konuşma metni boş.")
    return items


def split_source_items(
    items: list[SourceItem],
    *,
    target_seconds: int = 5 * 60,
    max_chars: int = 9000,
) -> list[list[SourceItem]]:
    """Split at cue boundaries, normally every five minutes or before context fills."""
    if target_seconds < 60 or target_seconds > 10 * 60:
        raise ValueError("Özet zaman parçası 1–10 dakika arasında olmalıdır.")
    if max_chars < 1000:
        raise ValueError("Özet parça boyutu en az 1000 karakter olmalıdır.")
    chunks: list[list[SourceItem]] = []
    current: list[SourceItem] = []
    current_chars = 0
    chunk_start = 0
    for item in items:
        item_chars = len(item.text) + len(item.id) + len(item.timestamp) + 16
        item_seconds = _seconds(item.timestamp)
        time_limit = bool(current and item_seconds - chunk_start >= target_seconds)
        context_limit = bool(current and current_chars + item_chars > max_chars)
        if time_limit or context_limit:
            chunks.append(current)
            current = []
            current_chars = 0
        if not current:
            chunk_start = item_seconds
        current.append(item)
        current_chars += item_chars
    if current:
        chunks.append(current)
    return chunks


def _json_payload(raw: str) -> dict[str, Any]:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as error:
        raise RuntimeError("Özet modeli geçerli JSON döndürmedi.") from error
    if not isinstance(payload, dict):
        raise RuntimeError("Özet modeli JSON nesnesi döndürmedi.")
    return payload


def _parse_claims(
    raw: str,
    *,
    allowed_source_ids: set[str],
    first_claim_number: int,
) -> list[EvidenceClaim]:
    records = _json_payload(raw).get("claims")
    if not isinstance(records, list):
        raise RuntimeError("Özet modeli kanıt iddiaları listesini döndürmedi.")
    claims: list[EvidenceClaim] = []
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        text = re.sub(r"\s+", " ", str(record.get("text") or "")).strip()
        raw_source_ids = record.get("source_ids")
        if not isinstance(raw_source_ids, list):
            continue
        source_ids = tuple(
            dict.fromkeys(str(item) for item in raw_source_ids if str(item) in allowed_source_ids)
        )
        if not text or len(text) > 800 or not source_ids or text.casefold() in seen:
            continue
        category = str(record.get("category") or "önemli_fikir")
        if category not in ALLOWED_CATEGORIES:
            category = "önemli_fikir"
        seen.add(text.casefold())
        claims.append(
            EvidenceClaim(
                f"C{first_claim_number + len(claims):06d}",
                text,
                category,
                source_ids,
            )
        )
    if not claims:
        raise RuntimeError("Özet modeli kaynakla ilişkilendirilebilir iddia üretmedi.")
    return claims


def _parse_final_sections(
    raw: str, claims: list[EvidenceClaim]
) -> dict[str, list[dict[str, object]]]:
    payload = _json_payload(raw)
    allowed_claim_ids = {claim.id for claim in claims}
    claim_map = {claim.id: claim for claim in claims}
    sections: dict[str, list[dict[str, object]]] = {}
    for section in FINAL_SECTIONS:
        records = payload.get(section)
        clean_records: list[dict[str, object]] = []
        if isinstance(records, list):
            for record in records:
                if isinstance(record, str) and record in allowed_claim_ids:
                    clean_records.append(
                        {
                            "text": claim_map[record].text,
                            "claim_ids": (record,),
                        }
                    )
                    continue
                if not isinstance(record, dict):
                    continue
                text = re.sub(r"\s+", " ", str(record.get("text") or "")).strip()
                raw_ids = record.get("claim_ids")
                if not isinstance(raw_ids, list):
                    continue
                claim_ids = tuple(
                    dict.fromkeys(str(item) for item in raw_ids if str(item) in allowed_claim_ids)
                )
                if text and len(text) <= 1000 and claim_ids:
                    clean_records.append({"text": text, "claim_ids": claim_ids})
        sections[section] = clean_records
    if not sections["short_summary"] or not sections["main_topics"]:
        raise RuntimeError("Özet modeli zorunlu final bölümlerini kanıtlarıyla döndürmedi.")
    return sections


def _fallback_sections(
    claims: list[EvidenceClaim],
) -> dict[str, list[dict[str, object]]]:
    """Keep summaries usable without ever accepting an unsupported final statement."""
    def records(category: str) -> list[dict[str, object]]:
        selected = [claim for claim in claims if claim.category == category]
        return [
            {"text": claim.text, "claim_ids": (claim.id,)} for claim in selected[:10]
        ]

    main = records("ana_konu") or [
        {"text": claim.text, "claim_ids": (claim.id,)} for claim in claims[:8]
    ]
    return {
        "short_summary": [
            {"text": claim.text, "claim_ids": (claim.id,)} for claim in claims[:3]
        ],
        "main_topics": main,
        "important_ideas": records("önemli_fikir"),
        "conclusions": records("sonuç"),
        "actions": records("eylem"),
    }


def _deduplicate_detailed_claims(
    sections: dict[str, list[dict[str, object]]],
    claims: list[EvidenceClaim],
) -> dict[str, list[dict[str, object]]]:
    """Keep a claim in at most one detailed section; short summary may repeat it."""
    result = {section: list(sections.get(section, ())) for section in FINAL_SECTIONS}
    destination = {
        "ana_konu": "main_topics",
        "önemli_fikir": "important_ideas",
        "sonuç": "conclusions",
        "eylem": "actions",
    }
    claim_map = {claim.id: claim for claim in claims}
    locations: dict[str, list[str]] = {}
    for section in FINAL_SECTIONS[1:]:
        for record in result[section]:
            for claim_id in record["claim_ids"]:
                locations.setdefault(str(claim_id), []).append(section)
    keep_in: dict[str, str] = {}
    for claim_id, found_sections in locations.items():
        preferred = destination[claim_map[claim_id].category]
        keep_in[claim_id] = (
            preferred if preferred in found_sections else found_sections[0]
        )
    seen: set[str] = set()
    for section in FINAL_SECTIONS[1:]:
        clean_records: list[dict[str, object]] = []
        for record in result[section]:
            claim_ids = tuple(
                str(claim_id)
                for claim_id in record["claim_ids"]
                if keep_in.get(str(claim_id)) == section and str(claim_id) not in seen
            )
            if not claim_ids:
                continue
            clean_records.append({"text": record["text"], "claim_ids": claim_ids})
            seen.update(claim_ids)
        result[section] = clean_records
    return result


def _safe_metadata(value: str | None, fallback: str) -> str:
    cleaned = re.sub(r"[\r\n\t]+", " ", value or "")
    return re.sub(r"\s+", " ", cleaned).strip()[:500] or fallback


def _normalize_title_names(text: str, source_title: str) -> str:
    """Repair close ASR spellings of distinctive mixed-case names in the title."""
    title_words = re.findall(r"[^\W_][\w.-]*", source_title, flags=re.UNICODE)
    canonical = [
        word
        for word in title_words
        if any(character.isupper() for character in word[1:])
        or (len(word) >= 3 and word.isupper())
    ]
    result = text
    for name in canonical:
        normalized_name = re.sub(r"[^\w]", "", name).casefold()
        words = list(re.finditer(r"[^\W_]+(?:[-.][^\W_]+)*", result, re.UNICODE))
        replacements: list[tuple[int, int]] = []
        occupied: set[int] = {
            index
            for index, word in enumerate(words)
            if re.sub(r"[^\w]", "", word.group()).casefold() == normalized_name
        }
        for size in (2, 1):
            for index in range(len(words) - size + 1):
                indexes = set(range(index, index + size))
                if indexes & occupied:
                    continue
                start = words[index].start()
                end = words[index + size - 1].end()
                candidate = result[start:end]
                normalized_candidate = re.sub(r"[^\w]", "", candidate).casefold()
                if normalized_candidate == normalized_name:
                    continue
                length_ratio = len(normalized_candidate) / max(len(normalized_name), 1)
                similarity = SequenceMatcher(
                    None, normalized_candidate, normalized_name
                ).ratio()
                if (
                    0.7 <= length_ratio <= 1.4
                    and normalized_candidate[:3] == normalized_name[:3]
                    and similarity >= 0.77
                ):
                    replacements.append((start, end))
                    occupied.update(indexes)
        for start, end in sorted(replacements, reverse=True):
            result = result[:start] + name + result[end:]
    return result


def _render_summary(
    sections: dict[str, list[dict[str, object]]],
    *,
    claims: list[EvidenceClaim],
    items: list[SourceItem],
    source_title: str,
    source_reference: str,
    model: str,
    transcript_sha256: str,
) -> tuple[str, dict[str, object], dict[str, object]]:
    claim_map = {claim.id: claim for claim in claims}
    source_map = {item.id: item for item in items}
    trace_records: list[dict[str, object]] = []

    def traced(record: dict[str, object], section: str) -> tuple[str, str]:
        claim_ids = tuple(str(value) for value in record["claim_ids"])
        source_ids = tuple(
            dict.fromkeys(
                source_id
                for claim_id in claim_ids
                for source_id in claim_map[claim_id].source_ids
            )
        )
        timestamps = tuple(dict.fromkeys(source_map[item].timestamp for item in source_ids))
        visible_timestamps = (
            timestamps
            if len(timestamps) <= 4
            else (*timestamps[:3], timestamps[-1])
        )
        citation = " ".join(f"[{timestamp}]" for timestamp in visible_timestamps)
        text = _normalize_title_names(str(record["text"]).strip(), source_title)
        trace_records.append(
            {
                "statement_id": f"T{len(trace_records) + 1:04d}",
                "section": section,
                "text": text,
                "claim_ids": list(claim_ids),
                "source_ids": list(source_ids),
                "timestamps": list(timestamps),
            }
        )
        return text, citation

    title_map = {
        "main_topics": "Ana konular",
        "important_ideas": "Önemli fikirler",
        "conclusions": "Sonuçlar / çıkarımlar",
        "actions": "Yapılacak işler / öneriler",
    }
    lines = ["# Türkçe Video Özeti", "", "## Kısa özet", ""]
    short_parts = []
    for record in sections["short_summary"]:
        text, citation = traced(record, "short_summary")
        short_parts.append(f"{text} {citation}")
    lines.extend((" ".join(short_parts), ""))
    for section, title in title_map.items():
        records = sections[section]
        if not records:
            continue
        lines.extend((f"## {title}", ""))
        for record in records:
            text, citation = traced(record, section)
            lines.append(f"- {text} — {citation}")
        lines.append("")

    # Important moments are derived from already validated statements, never
    # generated as a second unsupported set of claims.
    lines.extend(("## Zaman kodlu önemli anlar", ""))
    unique_moments: list[dict[str, object]] = []
    seen_moment_claims: set[tuple[str, ...]] = set()
    for record in trace_records:
        claim_ids = tuple(str(item) for item in record["claim_ids"])
        if claim_ids in seen_moment_claims:
            continue
        seen_moment_claims.add(claim_ids)
        unique_moments.append(record)
    moment_records = sorted(
        unique_moments,
        key=lambda record: _seconds(str(record["timestamps"][0])),
    )[:12]
    for record in moment_records:
        record_timestamps = tuple(str(item) for item in record["timestamps"])
        visible_timestamps = (
            record_timestamps
            if len(record_timestamps) <= 4
            else (*record_timestamps[:3], record_timestamps[-1])
        )
        timestamps = " ".join(f"[{item}]" for item in visible_timestamps)
        lines.append(f"- {timestamps} {record['text']}")
    lines.extend(
        (
            "",
            "## Kaynak ve üretim bilgisi",
            "",
            f"- Başlık: {_safe_metadata(source_title, 'Bilinmiyor')}",
            f"- Kaynak: {_safe_metadata(source_reference, 'Yerel dosya')}",
            f"- Model: `{_safe_metadata(model, 'Bilinmiyor')}`",
            f"- Transkript SHA-256: `{transcript_sha256}`",
            "",
        )
    )
    supported = sum(bool(item["source_ids"]) for item in trace_records)
    timestamped = sum(bool(item["timestamps"]) for item in trace_records)
    quality = {
        "schema_version": 1,
        "passed": bool(trace_records)
        and supported == len(trace_records)
        and timestamped == len(trace_records),
        "statement_count": len(trace_records),
        "supported_statement_count": supported,
        "timestamped_statement_count": timestamped,
        "source_coverage_percent": round(100 * supported / max(len(trace_records), 1), 2),
        "timestamp_coverage_percent": round(
            100 * timestamped / max(len(trace_records), 1), 2
        ),
        "semantic_review_required": True,
        "human_review_status": "pending",
        "errors": [],
        "warnings": (
            ["İnsan değerlendirmesi için 20'den az final iddiası var."]
            if len(trace_records) < 20
            else []
        ),
    }
    traceability = {
        "schema_version": 1,
        "source_item_count": len(items),
        "claim_count": len(claims),
        "selected_claim_count": len(
            {
                claim_id
                for record in trace_records
                for claim_id in record["claim_ids"]
            }
        ),
        "statements": trace_records,
        "claims": [claim.to_dict() for claim in claims],
    }
    return "\n".join(lines), traceability, quality


@serialized_model
def summarize_cues(
    cues: list[Cue],
    *,
    client: OllamaClient,
    source_title: str,
    source_reference: str,
    transcript_sha256: str,
    model: str = "qwen3.5:4b",
    max_chars: int = 9000,
    target_seconds: int = 5 * 60,
    completed_chunks: int = 0,
    initial_claims: list[EvidenceClaim] | None = None,
    on_checkpoint: object | None = None,
    on_progress: object | None = None,
) -> SummaryResult:
    """Build a Turkish summary whose every statement resolves to source cue IDs."""
    items = source_items_from_cues(cues)
    chunks = split_source_items(
        items, target_seconds=target_seconds, max_chars=max_chars
    )
    if completed_chunks < 0 or completed_chunks > len(chunks):
        raise ValueError("Özet checkpoint parça sayısı geçersiz.")
    claims = list(initial_claims or ())
    allowed_completed_ids = {
        item.id for chunk in chunks[:completed_chunks] for item in chunk
    }
    if any(
        not claim.source_ids
        or any(source_id not in allowed_completed_ids for source_id in claim.source_ids)
        for claim in claims
    ):
        raise ValueError("Özet checkpoint kaynak kimlikleri geçersiz.")
    system = (
        "Sen dikkatli bir Türkçe video editörüsün. Yalnız verilen transkript satırlarına "
        "dayan; dış bilgi, tahmin veya uydurma ekleme. Her iddiayı onu doğrudan destekleyen "
        "kaynak satırı kimlikleriyle ilişkilendir. Geçerli JSON dışında metin yazma."
    )
    total_steps = len(chunks) + 1
    for chunk_index, chunk in enumerate(chunks[completed_chunks:], start=completed_chunks):
        serialized = [asdict(item) for item in chunk]
        claim_schema = _claim_output_schema([item.id for item in chunk])
        prompt = (
            f"Bu transkriptin {chunk_index + 1}/{len(chunks)} parçasıdır. "
            "Ana konuları, önemli bilgileri, sonuçları ve açıkça söylenen eylemleri Türkçe "
            "3-6 kısa kanıt iddiasına dönüştür. Giriş/sunum cümlelerini ancak videonun ana "
            "mesajıysa al; aynı fikri farklı sözlerle tekrarlama. category=eylem yalnız "
            "konuşmada açık öneri, talimat veya yapılacak iş varsa kullanılmalı; bir aracın "
            "yapabildiğini anlatmak eylem değildir. Her iddia en fazla 180 karakter olsun. "
            "Yalnız iddianın tamamını doğrudan destekleyen 1-12 source_ids kullan; yorum, "
            "çıkarım veya kaynakta olmayan ayrıntı ekleme. Başlıkta geçen adların yazımını "
            f"koru: {_safe_metadata(source_title, 'Bilinmiyor')}. JSON biçimi: "
            '{"claims":[{"text":"...","category":"ana_konu|önemli_fikir|sonuç|eylem",'
            '"source_ids":["S000001"]}]}\nZORUNLU JSON ŞEMASI:\n'
            + json.dumps(claim_schema, ensure_ascii=False)
            + "\nINPUT:\n"
            + json.dumps(serialized, ensure_ascii=False)
        )
        raw = client.generate(
            model=model,
            prompt=prompt,
            system=system,
            json_schema=claim_schema,
            keep_alive="5m",
            max_tokens=1000,
        )
        claims.extend(
            _parse_claims(
                raw,
                allowed_source_ids={item.id for item in chunk},
                first_claim_number=len(claims) + 1,
            )
        )
        done = chunk_index + 1
        if callable(on_checkpoint):
            on_checkpoint(done, list(claims))
        if callable(on_progress):
            on_progress(SummaryProgress(done, total_steps))

    source_map = {item.id: item for item in items}
    notes = [
        {
            **claim.to_dict(),
            "evidence": [
                asdict(source_map[source_id]) for source_id in claim.source_ids
            ],
        }
        for claim in claims
    ]
    final_schema = _final_output_schema([claim.id for claim in claims])
    final_prompt = (
        "Aşağıdaki doğrulanmış kanıt iddialarını bölümlere seç. Yeni cümle yazma; yalnız "
        "claim id değerlerini kullan. short_summary seçilen 2-3 ana iddiayı tekrar edebilir; "
        "bunun dışında bir id en fazla bir ayrıntılı bölümde yer alsın. Bütün idleri kullanmak "
        "zorunda değilsin; giriş, bölüm geçişi, dolgu, tekrar veya tek başına yararsız kalan "
        "iddiaları seçme. Bir iddiadaki her olgu, yanındaki evidence metninde açıkça "
        "desteklenmiyorsa o iddiayı da seçme; yorumlayarak boşluk doldurma. short_summary "
        "2-3, main_topics en fazla 6, important_ideas en fazla 6, conclusions en fazla 4, "
        "actions en fazla 4 id içersin. Kaynakta açık eylem veya sonuç yoksa listeyi boş "
        "bırak. JSON biçimi: "
        '{"short_summary":["C000001"],"main_topics":["C000002"],'
        '"important_ideas":[],"conclusions":[],"actions":[]}\nZORUNLU JSON ŞEMASI:\n'
        + json.dumps(final_schema, ensure_ascii=False)
        + "\nKANITLAR:\n"
        + json.dumps(notes, ensure_ascii=False)
    )
    raw_final = client.generate(
        model=model,
        prompt=final_prompt,
        system=system,
        json_schema=final_schema,
        keep_alive=0,
        max_tokens=600,
    )
    try:
        sections = _parse_final_sections(raw_final, claims)
    except RuntimeError:
        sections = _fallback_sections(claims)
    sections = _deduplicate_detailed_claims(sections, claims)
    if callable(on_progress):
        on_progress(SummaryProgress(total_steps, total_steps))
    markdown, traceability, quality = _render_summary(
        sections,
        claims=claims,
        items=items,
        source_title=source_title,
        source_reference=source_reference,
        model=model,
        transcript_sha256=transcript_sha256,
    )
    return SummaryResult(
        markdown=markdown,
        traceability=traceability,
        quality=quality,
        claims=tuple(claims),
        chunk_count=len(chunks),
    )


def split_text(text: str, *, max_chars: int = 9000) -> list[str]:
    if max_chars < 1000:
        raise ValueError("Özet parça boyutu en az 1000 karakter olmalıdır.")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for line in lines:
        if current and length + len(line) + 1 > max_chars:
            chunks.append("\n".join(current))
            current, length = [], 0
        current.append(line)
        length += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def summarize_transcript(
    text: str,
    *,
    client: OllamaClient,
    model: str = "qwen3.5:4b",
    max_chars: int = 9000,
) -> str:
    chunks = split_text(text, max_chars=max_chars)
    if not chunks:
        raise ValueError("Özetlenecek konuşma metni boş.")
    partials: list[str] = []
    system = (
        "Sen dikkatli bir Türkçe editörsün. Yalnız verilen metne dayan; uydurma yapma. "
        "Ana fikirleri, önemli ayrıntıları, kararları ve eylemleri kısa ve açık yaz."
    )
    for index, chunk in enumerate(chunks, start=1):
        prompt = (
            f"Bu, video konuşmasının {index}/{len(chunks)} parçası. "
            "Türkçe olarak 5-10 maddede özetle. Tekrarları çıkar.\n\n" + chunk
        )
        partials.append(
            client.generate(model=model, prompt=prompt, system=system, keep_alive="5m")
        )
    final_prompt = (
        "Aşağıdaki parça özetlerini tek bir tutarlı Türkçe video özetine dönüştür. "
        "Önce 2-3 cümlelik kısa özet, sonra 'Ana noktalar' başlığı altında maddeler, "
        "varsa en sonda 'Eylemler / öneriler' bölümü olsun. Tekrarları çıkar ve metinde "
        "olmayan bilgi ekleme.\n\n" + "\n\n---\n\n".join(partials)
    )
    return client.generate(model=model, prompt=final_prompt, system=system, keep_alive=0)
