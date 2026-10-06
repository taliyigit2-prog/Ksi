"""Local document text extraction into a traceable canonical block stream."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import zipfile
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PdfReader

from ksi_local.atomic_files import atomic_write_json, atomic_write_text
from ksi_local.document_security import DocumentFormat, inspect_document
from ksi_local.language_detection import detect_document_language
from ksi_local.network_policy import local_worker_environment


SCHEMA_VERSION = 1
MAX_TOTAL_CHARACTERS = 5_000_000
MAX_BLOCK_CHARACTERS = 20_000
MIN_NATIVE_PDF_CHARACTERS = 24
LOW_OCR_CONFIDENCE = 0.65
OCR_PAGE_TIMEOUT_SECONDS = 120


@dataclass(frozen=True)
class CanonicalBlock:
    id: str
    sequence: int
    source_sha256: str
    source_format: str
    block_type: str
    text: str
    location: dict[str, object]
    extraction_method: str
    detected_language: str | None
    language_confidence: float | None
    ocr_confidence: float | None
    style: dict[str, object]
    flags: tuple[str, ...]
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ExtractionResult:
    source_sha256: str
    source_format: str
    block_count: int
    character_count: int
    jsonl_path: Path
    text_path: Path
    quality_path: Path
    quality: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "source_sha256": self.source_sha256,
            "source_format": self.source_format,
            "block_count": self.block_count,
            "character_count": self.character_count,
            "jsonl_path": str(self.jsonl_path),
            "text_path": str(self.text_path),
            "quality_path": str(self.quality_path),
            "quality": self.quality,
        }


@dataclass
class _DraftBlock:
    block_type: str
    text: str
    location: dict[str, object]
    extraction_method: str
    ocr_confidence: float | None = None
    style: dict[str, object] = field(default_factory=dict)
    flags: set[str] = field(default_factory=set)


def _clean_text(value: str, *, preserve_newlines: bool = False) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = "".join(
        character
        for character in value
        if character in {"\n", "\t"} or ord(character) >= 32
    )
    if preserve_newlines:
        return "\n".join(line.rstrip() for line in value.splitlines()).strip()
    return re.sub(r"\s+", " ", value).strip()


def _split_long_text(value: str) -> list[str]:
    text = value.strip()
    if len(text) <= MAX_BLOCK_CHARACTERS:
        return [text] if text else []
    pieces: list[str] = []
    while text:
        if len(text) <= MAX_BLOCK_CHARACTERS:
            pieces.append(text)
            break
        boundary = max(
            text.rfind("\n", 0, MAX_BLOCK_CHARACTERS + 1),
            text.rfind(". ", 0, MAX_BLOCK_CHARACTERS + 1),
            text.rfind("! ", 0, MAX_BLOCK_CHARACTERS + 1),
            text.rfind("? ", 0, MAX_BLOCK_CHARACTERS + 1),
            text.rfind(" ", 0, MAX_BLOCK_CHARACTERS + 1),
        )
        if boundary < MAX_BLOCK_CHARACTERS // 2:
            boundary = MAX_BLOCK_CHARACTERS
        elif text[boundary : boundary + 1] in {".", "!", "?"}:
            boundary += 1
        pieces.append(text[:boundary].strip())
        text = text[boundary:].strip()
    return [piece for piece in pieces if piece]


def _add_draft(
    drafts: list[_DraftBlock],
    *,
    block_type: str,
    text: str,
    location: dict[str, object],
    method: str,
    confidence: float | None = None,
    style: dict[str, object] | None = None,
    flags: Iterable[str] = (),
    preserve_newlines: bool = False,
) -> None:
    cleaned = _clean_text(text, preserve_newlines=preserve_newlines)
    parts = _split_long_text(cleaned)
    for part_index, part in enumerate(parts, start=1):
        part_location = dict(location)
        if len(parts) > 1:
            part_location["part"] = part_index
            part_location["part_count"] = len(parts)
        part_flags = set(flags)
        if len(parts) > 1:
            part_flags.add("long_block_split")
        drafts.append(
            _DraftBlock(
                block_type=block_type,
                text=part,
                location=part_location,
                extraction_method=method,
                ocr_confidence=confidence,
                style=dict(style or {}),
                flags=part_flags,
            )
        )


def _language_for(text: str) -> tuple[str | None, float | None]:
    letters = sum(character.isalpha() for character in text)
    if letters < 40:
        return None, None
    try:
        detection = detect_document_language(text[:50_000])
    except RuntimeError:
        return None, None
    return detection.code, round(detection.confidence, 4)


def _canonicalize(
    drafts: list[_DraftBlock], source_sha256: str, format: str
) -> list[CanonicalBlock]:
    blocks: list[CanonicalBlock] = []
    total = 0
    for sequence, draft in enumerate(drafts, start=1):
        total += len(draft.text)
        if total > MAX_TOTAL_CHARACTERS:
            raise ValueError("Belgeden çıkarılan metin 5 milyon karakter sınırını aşıyor.")
        language, confidence = _language_for(draft.text)
        blocks.append(
            CanonicalBlock(
                id=f"B{sequence:06d}",
                sequence=sequence,
                source_sha256=source_sha256,
                source_format=format,
                block_type=draft.block_type,
                text=draft.text,
                location=draft.location,
                extraction_method=draft.extraction_method,
                detected_language=language,
                language_confidence=confidence,
                ocr_confidence=(
                    round(draft.ocr_confidence, 4)
                    if draft.ocr_confidence is not None
                    else None
                ),
                style=draft.style,
                flags=tuple(sorted(draft.flags)),
            )
        )
    if not blocks:
        raise ValueError("Belgeden işlenebilir metin çıkarılamadı.")
    return blocks


def _run_vision_helper(
    helper: Path,
    arguments: list[str],
    *,
    timeout_seconds: int,
) -> dict[str, Any]:
    candidate = helper.expanduser()
    if candidate.is_symlink():
        raise RuntimeError("Yerel Apple Vision OCR yardımcısı sembolik bağlantı olamaz.")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise RuntimeError("Yerel Apple Vision OCR yardımcısı hazır değil.")
    completed = subprocess.run(
        [str(resolved), *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        env=local_worker_environment(),
        shell=False,
    )
    if completed.returncode != 0:
        message = completed.stderr.strip() or "Apple Vision OCR işlemi başarısız oldu."
        raise RuntimeError(message[:1_000])
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            "Apple Vision OCR yardımcısı geçersiz sonuç döndürdü."
        ) from error
    if not isinstance(payload, dict):
        raise RuntimeError("Apple Vision OCR yardımcısı beklenen sonucu döndürmedi.")
    return payload


def vision_capabilities(helper: str | Path) -> dict[str, object]:
    payload = _run_vision_helper(
        Path(helper), ["capabilities"], timeout_seconds=30
    )
    languages = payload.get("supported_languages")
    if not isinstance(languages, list) or not all(isinstance(item, str) for item in languages):
        raise RuntimeError("Apple Vision desteklenen dil listesini bildirmedi.")
    return {
        "engine": "macOS Vision",
        "revision": int(payload.get("revision") or 0),
        "supported_languages": tuple(languages),
        "automatic_language_detection": bool(payload.get("automatic_language_detection")),
    }


def _ocr_pdf_page(helper: Path, source: Path, page_index: int) -> list[dict[str, Any]]:
    payload = _run_vision_helper(
        helper,
        [
            "ocr",
            "--input",
            str(source),
            "--page-index",
            str(page_index),
            "--max-dimension",
            "2400",
        ],
        timeout_seconds=OCR_PAGE_TIMEOUT_SECONDS,
    )
    raw_lines = payload.get("lines")
    if not isinstance(raw_lines, list):
        raise RuntimeError("Apple Vision OCR satır listesi döndürmedi.")
    lines: list[dict[str, Any]] = []
    for raw in raw_lines:
        if not isinstance(raw, dict):
            continue
        text = _clean_text(str(raw.get("text") or ""))
        confidence = raw.get("confidence")
        if not text or not isinstance(confidence, (int, float)):
            continue
        lines.append(
            {
                "text": text,
                "confidence": max(0.0, min(float(confidence), 1.0)),
                "x": float(raw.get("x") or 0.0),
                "y": float(raw.get("y") or 0.0),
                "width": float(raw.get("width") or 0.0),
                "height": float(raw.get("height") or 0.0),
            }
        )
    return lines


def _pdf_native_text(page: object) -> str:
    extractor = getattr(page, "extract_text")
    try:
        result = extractor(extraction_mode="layout")
    except (KeyError, TypeError, ValueError):
        try:
            result = extractor()
        except KeyError:
            result = ""
    return str(result or "")


def _pdf_paragraphs(text: str) -> list[str]:
    normalized = _clean_text(text, preserve_newlines=True)
    if not normalized:
        return []
    paragraphs = [item.strip() for item in re.split(r"\n\s*\n", normalized) if item.strip()]
    if len(paragraphs) == 1:
        lines = [line.strip() for line in normalized.splitlines() if line.strip()]
        if len(lines) > 1:
            paragraphs = lines
    return paragraphs


def _margin_key(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def _flag_repeated_pdf_margins(drafts: list[_DraftBlock], page_count: int) -> int:
    if page_count < 2:
        return 0
    threshold = max(2, math.ceil(page_count * 0.5))
    flagged = 0
    for position, flag in (("first", "repeated_header"), ("last", "repeated_footer")):
        candidates = [
            draft
            for draft in drafts
            if draft.location.get("page_position") == position
            and 2 <= len(draft.text) <= 300
        ]
        counts = Counter(_margin_key(draft.text) for draft in candidates)
        repeated = {key for key, count in counts.items() if key and count >= threshold}
        for draft in candidates:
            if _margin_key(draft.text) in repeated:
                draft.flags.add(flag)
                flagged += 1
    return flagged


def _extract_pdf(
    source: Path,
    *,
    ocr_helper: Path | None,
    progress: Callable[[int, int], None] | None,
) -> tuple[list[_DraftBlock], dict[str, object]]:
    reader = PdfReader(source, strict=True)
    drafts: list[_DraftBlock] = []
    ocr_pages: list[int] = []
    native_pages: list[int] = []
    empty_pages: list[int] = []
    capabilities: dict[str, object] | None = None
    total_pages = len(reader.pages)
    for page_index, page in enumerate(reader.pages):
        page_number = page_index + 1
        native = _pdf_native_text(page)
        useful_characters = sum(character.isalnum() for character in native)
        page_drafts: list[_DraftBlock] = []
        if useful_characters >= MIN_NATIVE_PDF_CHARACTERS:
            native_pages.append(page_number)
            paragraphs = _pdf_paragraphs(native)
            for paragraph_index, paragraph in enumerate(paragraphs, start=1):
                _add_draft(
                    page_drafts,
                    block_type="paragraph",
                    text=paragraph,
                    location={"page": page_number, "paragraph": paragraph_index},
                    method="pypdf",
                )
        else:
            if ocr_helper is None:
                raise RuntimeError(
                    f"PDF sayfa {page_number} taranmış görünüyor; "
                    "Apple Vision OCR yardımcısı bulunamadı."
                )
            if capabilities is None:
                capabilities = vision_capabilities(ocr_helper)
            lines = _ocr_pdf_page(ocr_helper, source, page_index)
            if lines:
                ocr_pages.append(page_number)
                for line_index, line in enumerate(lines, start=1):
                    flags = {"ocr"}
                    if line["confidence"] < LOW_OCR_CONFIDENCE:
                        flags.add("low_ocr_confidence")
                    _add_draft(
                        page_drafts,
                        block_type="ocr_line",
                        text=str(line["text"]),
                        location={
                            "page": page_number,
                            "line": line_index,
                            "bounding_box": {
                                key: round(float(line[key]), 6)
                                for key in ("x", "y", "width", "height")
                            },
                        },
                        method="vision_ocr",
                        confidence=float(line["confidence"]),
                        flags=flags,
                    )
            else:
                empty_pages.append(page_number)
        if page_drafts:
            page_drafts[0].location["page_position"] = "first"
            page_drafts[-1].location["page_position"] = "last"
            drafts.extend(page_drafts)
        if progress is not None:
            progress(page_number, max(total_pages, 1))
    repeated = _flag_repeated_pdf_margins(drafts, total_pages)
    return drafts, {
        "page_count": total_pages,
        "native_text_pages": native_pages,
        "ocr_pages": ocr_pages,
        "empty_pages": empty_pages,
        "repeated_margin_block_count": repeated,
        "vision": capabilities,
    }


def _paragraph_type(paragraph: Paragraph) -> tuple[str, dict[str, object]]:
    style_name = str(paragraph.style.name if paragraph.style is not None else "")
    style: dict[str, object] = {"paragraph_style": style_name} if style_name else {}
    if style_name.casefold().startswith("heading") or style_name.casefold() == "title":
        match = re.search(r"(\d+)", style_name)
        if match:
            style["heading_level"] = int(match.group(1))
        return "heading", style
    properties = paragraph._p.pPr
    if properties is not None and properties.numPr is not None:
        return "list_item", style
    return "paragraph", style


def _docx_paragraph(
    drafts: list[_DraftBlock],
    paragraph: Paragraph,
    *,
    location: dict[str, object],
    flags: Iterable[str] = (),
) -> None:
    block_type, style = _paragraph_type(paragraph)
    _add_draft(
        drafts,
        block_type=block_type,
        text=paragraph.text,
        location=location,
        method="python-docx",
        style=style,
        flags=flags,
    )


def _docx_table(
    drafts: list[_DraftBlock],
    table: Table,
    *,
    table_index: int,
    location_prefix: dict[str, object],
    depth: int = 1,
) -> None:
    if depth > 3:
        raise ValueError("DOCX iç içe tablo derinliği üç seviyelik sınırı aşıyor.")
    # Retain the XML nodes themselves. Short-lived python-docx wrappers can be
    # released between cells and CPython may reuse their numeric ``id`` value.
    seen_cells: set[object] = set()
    for row_index, row in enumerate(table.rows, start=1):
        for column_index, cell in enumerate(row.cells, start=1):
            identity = cell._tc
            if identity in seen_cells:
                continue
            seen_cells.add(identity)
            location = {
                **location_prefix,
                "table": table_index,
                "row": row_index,
                "column": column_index,
                "depth": depth,
            }
            text = "\n".join(
                paragraph.text for paragraph in cell.paragraphs if paragraph.text.strip()
            )
            _add_draft(
                drafts,
                block_type="table_cell",
                text=text,
                location=location,
                method="python-docx",
                style={"table_style": str(table.style.name if table.style is not None else "")},
            )
            for nested_index, nested in enumerate(cell.tables, start=1):
                _docx_table(
                    drafts,
                    nested,
                    table_index=nested_index,
                    location_prefix={**location, "parent_table": table_index},
                    depth=depth + 1,
                )


def _docx_auxiliary_part(
    drafts: list[_DraftBlock],
    container: object,
    *,
    part_name: str,
    section_index: int,
) -> None:
    paragraphs = getattr(container, "paragraphs", ())
    for paragraph_index, paragraph in enumerate(paragraphs, start=1):
        _docx_paragraph(
            drafts,
            paragraph,
            location={
                "part": part_name,
                "section": section_index,
                "paragraph": paragraph_index,
            },
            flags=(part_name,),
        )
    for table_index, table in enumerate(getattr(container, "tables", ()), start=1):
        _docx_table(
            drafts,
            table,
            table_index=table_index,
            location_prefix={"part": part_name, "section": section_index},
        )


def _extract_docx_footnotes(source: Path, drafts: list[_DraftBlock]) -> int:
    with zipfile.ZipFile(source) as package:
        names = {name.casefold(): name for name in package.namelist()}
        actual = names.get("word/footnotes.xml")
        if actual is None:
            return 0
        content = package.read(actual)
    if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
        raise ValueError("DOCX dipnot XML'i güvenli olmayan varlık tanımı içeriyor.")
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as error:
        raise ValueError("DOCX dipnot XML yapısı bozuk.") from error
    count = 0
    for footnote in root:
        footnote_id = next(
            (value for key, value in footnote.attrib.items() if key.rsplit("}", 1)[-1] == "id"),
            None,
        )
        if footnote_id in {None, "-1", "0"}:
            continue
        paragraph_index = 0
        for paragraph in footnote.iter():
            if paragraph.tag.rsplit("}", 1)[-1] != "p":
                continue
            paragraph_index += 1
            text = "".join(
                node.text or ""
                for node in paragraph.iter()
                if node.tag.rsplit("}", 1)[-1] in {"t", "delText"}
            )
            before = len(drafts)
            _add_draft(
                drafts,
                block_type="footnote",
                text=text,
                location={
                    "part": "footnote",
                    "footnote_id": str(footnote_id),
                    "paragraph": paragraph_index,
                },
                method="ooxml",
                flags=("footnote",),
            )
            count += len(drafts) - before
    return count


def _extract_docx(
    source: Path,
    *,
    progress: Callable[[int, int], None] | None,
) -> tuple[list[_DraftBlock], dict[str, object]]:
    document = Document(source)
    drafts: list[_DraftBlock] = []
    paragraph_index = 0
    table_index = 0
    body_items = list(document.iter_inner_content())
    for item_index, item in enumerate(body_items, start=1):
        if isinstance(item, Paragraph):
            paragraph_index += 1
            _docx_paragraph(
                drafts,
                item,
                location={"part": "body", "paragraph": paragraph_index},
            )
        elif isinstance(item, Table):
            table_index += 1
            _docx_table(
                drafts,
                item,
                table_index=table_index,
                location_prefix={"part": "body"},
            )
        if progress is not None:
            progress(item_index, max(len(body_items), 1))

    seen_parts: set[str] = set()
    header_blocks_before = len(drafts)
    for section_index, section in enumerate(document.sections, start=1):
        for part_name, container in (("header", section.header), ("footer", section.footer)):
            part_id = str(getattr(container.part, "partname", f"{part_name}-{section_index}"))
            if part_id in seen_parts:
                continue
            seen_parts.add(part_id)
            _docx_auxiliary_part(
                drafts,
                container,
                part_name=part_name,
                section_index=section_index,
            )
    header_footer_count = len(drafts) - header_blocks_before
    footnote_count = _extract_docx_footnotes(source, drafts)
    raw_xml = document.element.xml
    warnings: list[str] = []
    if any(marker in raw_xml for marker in ("<w:ins", "<w:del", "<w:moveFrom", "<w:moveTo")):
        warnings.append(
            "DOCX değişiklik izleme öğeleri içeriyor; çıkarılan görünür metin "
            "elle kontrol edilmeli."
        )
    if any(marker in raw_xml for marker in ("<w:txbxContent", "<m:oMath", "<m:oMathPara")):
        warnings.append(
            "DOCX metin kutusu veya denklem içeriyor; karmaşık öğeler ayrıca işaretlendi."
        )
    return drafts, {
        "body_paragraph_count": paragraph_index,
        "body_table_count": table_index,
        "header_footer_block_count": header_footer_count,
        "footnote_block_count": footnote_count,
        "warnings": warnings,
    }


_MARKDOWN_LINK = re.compile(r"!?\[([^\]]*)\]\(([^)\s]+)(?:\s+[\"'][^\"']*[\"'])?\)")


def _markdown_links(text: str) -> list[dict[str, str]]:
    return [
        {"label": match.group(1), "target": match.group(2)}
        for match in _MARKDOWN_LINK.finditer(text)
    ]


def _extract_markdown(source: Path) -> tuple[list[_DraftBlock], dict[str, object]]:
    text = source.read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    drafts: list[_DraftBlock] = []
    index = 0
    paragraph: list[str] = []
    paragraph_start = 1
    link_count = 0
    table_count = 0

    def flush_paragraph(end_line: int) -> None:
        nonlocal paragraph, link_count
        if not paragraph:
            return
        value = "\n".join(paragraph)
        links = _markdown_links(value)
        link_count += len(links)
        _add_draft(
            drafts,
            block_type="paragraph",
            text=value,
            location={"line_start": paragraph_start, "line_end": end_line},
            method="markdown",
            style={"links": links} if links else {},
            preserve_newlines=True,
        )
        paragraph = []

    while index < len(lines):
        line_number = index + 1
        line = lines[index]
        fence = re.match(r"^\s*(`{3,}|~{3,})(.*)$", line)
        if fence:
            flush_paragraph(line_number - 1)
            marker = fence.group(1)
            language = fence.group(2).strip()
            code: list[str] = []
            start = line_number
            index += 1
            while index < len(lines) and not re.match(
                rf"^\s*{re.escape(marker[0])}{{{len(marker)},}}\s*$", lines[index]
            ):
                code.append(lines[index])
                index += 1
            closed = index < len(lines)
            end = index + 1 if closed else len(lines)
            _add_draft(
                drafts,
                block_type="code_block",
                text="\n".join(code),
                location={"line_start": start, "line_end": end},
                method="markdown",
                style={"language": language} if language else {},
                flags=() if closed else ("unclosed_code_fence",),
                preserve_newlines=True,
            )
            index += 1 if closed else 0
            continue
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if heading:
            flush_paragraph(line_number - 1)
            links = _markdown_links(heading.group(2))
            link_count += len(links)
            _add_draft(
                drafts,
                block_type="heading",
                text=heading.group(2),
                location={"line_start": line_number, "line_end": line_number},
                method="markdown",
                style={"heading_level": len(heading.group(1)), "links": links},
            )
            index += 1
            continue
        list_item = re.match(r"^(\s*)(?:[-*+] |\d+[.)] )(.+)$", line)
        if list_item:
            flush_paragraph(line_number - 1)
            content = list_item.group(2)
            links = _markdown_links(content)
            link_count += len(links)
            _add_draft(
                drafts,
                block_type="list_item",
                text=content,
                location={"line_start": line_number, "line_end": line_number},
                method="markdown",
                style={"indent": len(list_item.group(1)), "links": links},
            )
            index += 1
            continue
        if "|" in line and index + 1 < len(lines) and re.match(
            r"^\s*\|?\s*:?-{3,}", lines[index + 1]
        ):
            flush_paragraph(line_number - 1)
            table_count += 1
            table_start = line_number
            rows: list[str] = [line]
            index += 2
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                rows.append(lines[index])
                index += 1
            for row_index, row in enumerate(rows, start=1):
                cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
                for column_index, cell in enumerate(cells, start=1):
                    links = _markdown_links(cell)
                    link_count += len(links)
                    _add_draft(
                        drafts,
                        block_type="table_cell",
                        text=cell,
                        location={
                            "line_start": (
                                table_start if row_index == 1 else table_start + row_index
                            ),
                            "line_end": (
                                table_start if row_index == 1 else table_start + row_index
                            ),
                            "table": table_count,
                            "row": row_index,
                            "column": column_index,
                        },
                        method="markdown",
                        style={"header": row_index == 1, "links": links},
                    )
            continue
        reference = re.match(r"^\s*\[([^\]]+)\]:\s*(\S+)", line)
        if reference:
            flush_paragraph(line_number - 1)
            link_count += 1
            _add_draft(
                drafts,
                block_type="link_definition",
                text=reference.group(1),
                location={"line_start": line_number, "line_end": line_number},
                method="markdown",
                style={"target": reference.group(2)},
            )
            index += 1
            continue
        if not line.strip():
            flush_paragraph(line_number - 1)
        else:
            if not paragraph:
                paragraph_start = line_number
            paragraph.append(line)
        index += 1
    flush_paragraph(len(lines))
    return drafts, {"line_count": len(lines), "link_target_count": link_count}


def _extract_text(source: Path) -> tuple[list[_DraftBlock], dict[str, object]]:
    content = source.read_bytes()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError(
            "TXT kodlaması belirsiz; belgeyi UTF-8 olarak kaydedip yeniden deneyin."
        ) from error
    parseable = text.rstrip()
    drafts: list[_DraftBlock] = []
    cursor = 0
    matches = re.finditer(r"\S(?:.*?\S)?(?=\n\s*\n|\Z)", parseable, re.S)
    for block_index, match in enumerate(matches, start=1):
        start = text.count("\n", 0, match.start()) + 1
        end = start + match.group(0).count("\n")
        _add_draft(
            drafts,
            block_type="paragraph",
            text=match.group(0),
            location={"block": block_index, "line_start": start, "line_end": end},
            method="utf8",
            preserve_newlines=True,
        )
        cursor = match.end()
    return drafts, {"line_count": text.count("\n") + 1, "parsed_character_count": cursor}


def _read_back_jsonl(path: Path, expected: list[CanonicalBlock]) -> None:
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise RuntimeError("Kanonik belge çıktısı JSON nesnesi değil.")
        records.append(payload)
    if len(records) != len(expected):
        raise RuntimeError("Kanonik belge çıktısında blok sayısı değişti.")
    for record, block in zip(records, expected, strict=True):
        if (
            record.get("id") != block.id
            or record.get("source_sha256") != block.source_sha256
            or record.get("text") != block.text
        ):
            raise RuntimeError(
                "Kanonik belge çıktısı yeniden okuma doğrulamasından geçmedi."
            )


def _readable_text(source: Path, blocks: list[CanonicalBlock]) -> str:
    lines = [
        "KSI Local Studio Belge Kaynağı",
        f"Kaynak dosya: {source.name}",
        f"Kaynak SHA-256: {blocks[0].source_sha256}",
        f"Blok sayısı: {len(blocks)}",
        "",
    ]
    for block in blocks:
        location = ", ".join(f"{key}={value}" for key, value in block.location.items())
        flags = f" · işaretler={','.join(block.flags)}" if block.flags else ""
        lines.extend(
            [
                f"[{block.id}] {block.block_type} · {location} · {block.extraction_method}{flags}",
                block.text,
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def extract_document(
    source: str | Path,
    output_directory: str | Path,
    *,
    expected_sha256: str | None = None,
    ocr_helper: str | Path | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> ExtractionResult:
    """Extract a previously inspected document without network or model use."""
    source_candidate = Path(source).expanduser()
    if source_candidate.is_symlink():
        raise ValueError("Belge kaynağı sembolik bağlantı olamaz.")
    source_path = source_candidate.resolve(strict=True)
    output_candidate = Path(output_directory).expanduser()
    if output_candidate.is_symlink():
        raise ValueError("Belge çıktı klasörü sembolik bağlantı olamaz.")
    output = output_candidate.resolve()
    inspection = inspect_document(source_path)
    if not inspection.accepted:
        raise ValueError("Belge güvenlik ön incelemesini geçmedi.")
    if expected_sha256 and inspection.sha256 != expected_sha256.casefold():
        raise OSError("Belge içe aktarma manifestiyle eşleşmiyor.")

    if inspection.format is DocumentFormat.PDF:
        drafts, details = _extract_pdf(
            source_path,
            ocr_helper=Path(ocr_helper) if ocr_helper else None,
            progress=progress,
        )
    elif inspection.format is DocumentFormat.DOCX:
        drafts, details = _extract_docx(source_path, progress=progress)
    elif inspection.format is DocumentFormat.MARKDOWN:
        drafts, details = _extract_markdown(source_path)
        if progress is not None:
            progress(1, 1)
    else:
        drafts, details = _extract_text(source_path)
        if progress is not None:
            progress(1, 1)

    blocks = _canonicalize(drafts, inspection.sha256, inspection.format.value)
    character_count = sum(len(block.text) for block in blocks)
    combined_language, combined_confidence = _language_for(
        "\n".join(block.text for block in blocks)[:100_000]
    )
    low_ocr = sum("low_ocr_confidence" in block.flags for block in blocks)
    ocr_blocks = [block for block in blocks if block.ocr_confidence is not None]
    ocr_average = (
        round(sum(block.ocr_confidence or 0 for block in ocr_blocks) / len(ocr_blocks), 4)
        if ocr_blocks
        else None
    )
    warnings = list(details.pop("warnings", []))
    if low_ocr:
        warnings.append(f"{low_ocr} OCR satırı düşük güven nedeniyle elle kontrol edilmeli.")
    if details.get("empty_pages"):
        warnings.append("Bazı PDF sayfalarında metin veya OCR sonucu bulunamadı.")
    quality: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "passed": True,
        "semantic_review_required": True,
        "source_filename": source_path.name,
        "source_sha256": inspection.sha256,
        "source_format": inspection.format.value,
        "block_count": len(blocks),
        "character_count": character_count,
        "document_language": combined_language,
        "document_language_confidence": combined_confidence,
        "ocr_block_count": len(ocr_blocks),
        "low_ocr_confidence_block_count": low_ocr,
        "ocr_average_confidence": ocr_average,
        "warnings": warnings,
        "details": details,
    }
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    jsonl_path = output / "belge-kaynagi.jsonl"
    text_path = output / "belge-kaynagi.txt"
    quality_path = output / "belge-kaynagi.kalite.json"
    jsonl = "\n".join(
        json.dumps(block.to_dict(), ensure_ascii=False, sort_keys=True) for block in blocks
    ) + "\n"
    atomic_write_text(jsonl_path, jsonl)
    _read_back_jsonl(jsonl_path, blocks)
    atomic_write_text(text_path, _readable_text(source_path, blocks))
    quality["jsonl_sha256"] = hashlib.sha256(jsonl_path.read_bytes()).hexdigest()
    quality["text_sha256"] = hashlib.sha256(text_path.read_bytes()).hexdigest()
    atomic_write_json(quality_path, quality)
    return ExtractionResult(
        source_sha256=inspection.sha256,
        source_format=inspection.format.value,
        block_count=len(blocks),
        character_count=character_count,
        jsonl_path=jsonl_path,
        text_path=text_path,
        quality_path=quality_path,
        quality=quality,
    )
