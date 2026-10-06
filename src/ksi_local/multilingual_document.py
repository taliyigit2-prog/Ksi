"""Language-coded document translation built on the verified local subtitle translator."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ksi_local.atomic_files import atomic_write_json, atomic_write_text
from ksi_local.document_output import create_document_outputs
from ksi_local.document_translation import _canonical_blocks
from ksi_local.ollama_client import OllamaClient
from ksi_local.subtitles import Cue
from ksi_local.translation import translate_cues
from ksi_local.translation_targets import normalize_target_language, protected_values_preserved


@dataclass(frozen=True)
class MultilingualDocumentResult:
    target_language: str
    jsonl_path: Path
    text_path: Path
    markdown_path: Path
    docx_path: Path
    pdf_path: Path
    quality_path: Path


def translate_document_target(
    canonical_path: str | Path,
    output_directory: str | Path,
    *,
    source_language: str,
    target_language: str,
    client: OllamaClient,
    source_title: str | None = None,
) -> MultilingualDocumentResult:
    target = normalize_target_language(target_language)
    canonical, canonical_sha256, blocks = _canonical_blocks(canonical_path)
    translatable = [block for block in blocks if block["block_type"] != "code_block"]
    cues = [
        Cue(index, "00:00:00,000", "00:00:01,000", str(block["text"]))
        for index, block in enumerate(translatable, start=1)
    ]
    translated = translate_cues(
        cues,
        source_language=source_language,
        target_language=target,
        client=client,
    )
    mapping = {
        id(block): cue.text
        for block, cue in zip(translatable, translated, strict=True)
    }
    records: list[dict[str, object]] = []
    for block in blocks:
        source_text = str(block["text"])
        target_text = mapping.get(id(block), source_text)
        if not protected_values_preserved(source_text, target_text):
            raise RuntimeError("Belge çevirisi sayı, ad veya URL koruma kapısından geçmedi.")
        records.append({
            **block,
            "source_text": source_text,
            "translated_text": target_text,
            "target_language": target,
            "status": "preserve" if block["block_type"] == "code_block" or source_language == target else "translate",
        })
    output = Path(output_directory).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    staging = Path(tempfile.mkdtemp(prefix=f".belge-{target}-", dir=output))
    try:
        jsonl_staged = staging / f"belge.{target}.jsonl"
        atomic_write_text(
            jsonl_staged,
            "\n".join(json.dumps(item, ensure_ascii=False, sort_keys=True) for item in records) + "\n",
        )
        rendered = create_document_outputs(
            records, staging, source_title=source_title or canonical.name
        )
        destinations = {
            "jsonl": output / f"belge.{target}.jsonl",
            "text": output / f"belge.{target}.txt",
            "markdown": output / f"belge.{target}.md",
            "docx": output / f"belge.{target}.docx",
            "pdf": output / f"belge.{target}.pdf",
            "quality": output / f"belge.{target}.kalite.json",
        }
        quality = {
            "schema_version": 1,
            "canonical_sha256": canonical_sha256,
            "source_language": source_language,
            "target_language": target,
            "block_count": len(records),
            "protected_values_preserved": True,
        }
        atomic_write_json(staging / destinations["quality"].name, quality)
        sources = {
            "jsonl": jsonl_staged,
            "text": rendered.text_path,
            "markdown": rendered.markdown_path,
            "docx": rendered.docx_path,
            "pdf": rendered.pdf_path,
            "quality": staging / destinations["quality"].name,
        }
        for key, destination in destinations.items():
            os.replace(sources[key], destination)
        if target == "tr":
            legacy = {
                "jsonl": output / "belge-turkce.jsonl", "text": output / "belge-turkce.txt",
                "markdown": output / "belge-turkce.md", "docx": output / "belge-turkce.docx",
                "pdf": output / "belge-turkce.pdf", "quality": output / "belge-turkce.kalite.json",
            }
            for key, path in legacy.items():
                shutil.copy2(destinations[key], path)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return MultilingualDocumentResult(target, **{f"{key}_path": value for key, value in destinations.items()})
