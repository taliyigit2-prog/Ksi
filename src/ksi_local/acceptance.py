"""Deterministic three-hour timeline acceptance without loading AI models."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from ksi_local.glossary import empty_glossary
from ksi_local.storage import estimate_video_storage
from ksi_local.subtitle_quality import assess_subtitle_quality
from ksi_local.subtitles import Cue, read_srt, write_srt
from ksi_local.summarization import source_items_from_cues, split_source_items


THREE_HOURS_SECONDS = 3 * 60 * 60
SYNTHETIC_CUE_SECONDS = 5


@dataclass(frozen=True)
class LongTimelineAcceptance:
    duration_seconds: int
    cue_count: int
    summary_chunk_count: int
    srt_roundtrip_ok: bool
    timestamp_structure_ok: bool
    storage_required_bytes: int
    storage_fits: bool
    passed: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _timestamp(seconds: int) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes, second = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{second:02d},000"


def run_three_hour_timeline_acceptance(
    scratch_directory: str | Path, *, free_bytes: int
) -> LongTimelineAcceptance:
    """Exercise SRT, quality, chunking and storage boundaries at exactly three hours."""
    scratch = Path(scratch_directory).expanduser().resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    cue_count = THREE_HOURS_SECONDS // SYNTHETIC_CUE_SECONDS
    source: list[Cue] = []
    translated: list[Cue] = []
    for offset in range(0, THREE_HOURS_SECONDS, SYNTHETIC_CUE_SECONDS):
        index = len(source) + 1
        start = _timestamp(offset)
        end = _timestamp(offset + SYNTHETIC_CUE_SECONDS)
        source.append(Cue(index, start, end, f"Source segment {index}."))
        translated.append(Cue(index, start, end, f"Türkçe bölüm {index}."))
    target = scratch / "uc-saat-pilot.srt"
    write_srt(target, translated)
    loaded = read_srt(target)
    quality = assess_subtitle_quality(
        source,
        loaded,
        source_language="en",
        glossary=empty_glossary("en"),
    )
    chunks = split_source_items(source_items_from_cues(source))
    budget = estimate_video_storage(
        THREE_HOURS_SECONDS,
        free_bytes=free_bytes,
        missing_model_bytes=0,
    )
    roundtrip_ok = bool(
        len(loaded) == cue_count
        and loaded[0].start == "00:00:00,000"
        and loaded[-1].end == "03:00:00,000"
    )
    passed = bool(roundtrip_ok and quality.passed and budget.fits and len(chunks) >= 36)
    return LongTimelineAcceptance(
        duration_seconds=THREE_HOURS_SECONDS,
        cue_count=cue_count,
        summary_chunk_count=len(chunks),
        srt_roundtrip_ok=roundtrip_ok,
        timestamp_structure_ok=quality.passed,
        storage_required_bytes=budget.required_bytes,
        storage_fits=budget.fits,
        passed=passed,
    )
