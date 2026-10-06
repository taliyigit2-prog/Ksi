"""Small, dependency-free SRT parser and writer."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path

from ksi_local.atomic_files import atomic_write_text
from ksi_local.languages import AUTO_LANGUAGE, SUPPORTED_SOURCE_LANGUAGES

_TIMING = re.compile(
    r"^(?P<start>\d{2}:\d{2}:\d{2},\d{3})\s+-->\s+"
    r"(?P<end>\d{2}:\d{2}:\d{2},\d{3})(?P<settings>.*)$"
)
_VTT_TIMING = re.compile(
    r"^(?P<start>(?:\d{2}:)?\d{2}:\d{2}[.,]\d{3})\s+-->\s+"
    r"(?P<end>(?:\d{2}:)?\d{2}:\d{2}[.,]\d{3})(?P<settings>.*)$"
)
TEXT_SOURCE_SUFFIXES = (".srt", ".vtt", ".txt")
MAX_TEXT_SOURCE_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class Cue:
    index: int
    start: str
    end: str
    text: str
    settings: str = ""

    def with_text(self, text: str) -> "Cue":
        return replace(self, text=text.strip())


@dataclass(frozen=True)
class SubtitleSelection:
    path: Path
    detected_language: str
    reason: str


def parse_srt_text(content: str) -> list[Cue]:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    blocks = re.split(r"\n{2,}", normalized.strip()) if normalized.strip() else []
    cues: list[Cue] = []
    for block in blocks:
        lines = block.splitlines()
        if len(lines) < 3:
            continue
        try:
            index = int(lines[0].strip())
        except ValueError:
            continue
        match = _TIMING.match(lines[1].strip())
        if match is None:
            continue
        text = "\n".join(lines[2:]).strip()
        if text:
            cues.append(
                Cue(
                    index=index,
                    start=match.group("start"),
                    end=match.group("end"),
                    text=text,
                    settings=match.group("settings"),
                )
            )
    if not cues:
        raise ValueError("SRT içinde geçerli altyazı satırı bulunamadı.")
    return cues


def read_srt(path: str | Path) -> list[Cue]:
    source = Path(path)
    if not source.is_file() or source.stat().st_size > MAX_TEXT_SOURCE_BYTES:
        raise ValueError("SRT kaynağı normal dosya ve güvenli metin boyutu içinde olmalıdır.")
    return parse_srt_text(source.read_text(encoding="utf-8-sig"))


def _srt_timestamp(value: str) -> str:
    normalized = value.replace(".", ",")
    if normalized.count(":") == 1:
        normalized = f"00:{normalized}"
    return normalized


def parse_vtt_text(content: str) -> list[Cue]:
    """Parse ordinary WebVTT cues into the program's canonical SRT cue model."""
    normalized = content.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    lines = normalized.splitlines()
    cues: list[Cue] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line or line == "WEBVTT" or line.startswith(("NOTE", "STYLE", "REGION")):
            index += 1
            if line.startswith(("NOTE", "STYLE", "REGION")):
                while index < len(lines) and lines[index].strip():
                    index += 1
            continue
        match = _VTT_TIMING.match(line)
        if match is None and index + 1 < len(lines):
            match = _VTT_TIMING.match(lines[index + 1].strip())
            if match is not None:
                index += 1
        if match is None:
            index += 1
            continue
        index += 1
        text_lines: list[str] = []
        while index < len(lines) and lines[index].strip():
            text_lines.append(lines[index].strip())
            index += 1
        text = "\n".join(text_lines).strip()
        if text:
            cues.append(
                Cue(
                    index=len(cues) + 1,
                    start=_srt_timestamp(match.group("start")),
                    end=_srt_timestamp(match.group("end")),
                    text=text,
                    settings=match.group("settings"),
                )
            )
    if not cues:
        raise ValueError("VTT içinde geçerli altyazı satırı bulunamadı.")
    return cues


def _timestamp_from_seconds(value: int) -> str:
    hours, remainder = divmod(value, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},000"


def parse_plain_transcript(content: str) -> list[Cue]:
    """Create synthetic, clearly documented cue times for a plain transcript."""
    normalized = content.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    paragraphs = [re.sub(r"\s+", " ", item).strip() for item in re.split(r"\n+", normalized)]
    chunks: list[str] = []
    for paragraph in (item for item in paragraphs if item):
        remaining = paragraph
        while len(remaining) > 450:
            boundary = max(
                remaining.rfind(". ", 180, 450),
                remaining.rfind("? ", 180, 450),
                remaining.rfind("! ", 180, 450),
                remaining.rfind(" ", 180, 450),
            )
            boundary = boundary + 1 if boundary >= 180 else 450
            chunks.append(remaining[:boundary].strip())
            remaining = remaining[boundary:].strip()
        if remaining:
            chunks.append(remaining)
    if not chunks:
        raise ValueError("TXT konuşma dökümü boş.")
    cues: list[Cue] = []
    elapsed = 0
    for paragraph in chunks:
        # About 15 readable characters per second, bounded to useful cue lengths.
        duration = min(30, max(4, round(len(paragraph) / 15)))
        if elapsed + duration > 3 * 60 * 60:
            raise ValueError("TXT konuşma dökümü üç saatlik iş sınırını aşıyor.")
        cues.append(
            Cue(
                index=len(cues) + 1,
                start=_timestamp_from_seconds(elapsed),
                end=_timestamp_from_seconds(elapsed + duration),
                text=paragraph,
            )
        )
        elapsed += duration
    return cues


def read_text_source(path: str | Path) -> list[Cue]:
    source = Path(path)
    if source.stat().st_size > MAX_TEXT_SOURCE_BYTES:
        raise ValueError("Altyazı/konuşma dökümü en fazla 10 MiB olabilir.")
    suffix = source.suffix.casefold()
    content = source.read_text(encoding="utf-8-sig")
    if suffix == ".srt":
        return parse_srt_text(content)
    if suffix == ".vtt":
        return parse_vtt_text(content)
    if suffix == ".txt":
        return parse_plain_transcript(content)
    raise ValueError("Yalnız SRT, VTT veya TXT metin kaynakları desteklenir.")


def convert_text_source_to_srt(source: str | Path, target: str | Path) -> list[Cue]:
    cues = read_text_source(source)
    write_srt(target, cues)
    return cues


def write_srt(path: str | Path, cues: list[Cue]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    blocks = []
    for output_index, cue in enumerate(cues, start=1):
        blocks.append(
            f"{output_index}\n{cue.start} --> {cue.end}{cue.settings}\n{cue.text.strip()}"
        )
    atomic_write_text(target, "\n\n".join(blocks) + "\n")


def _filename_language(path: Path) -> str | None:
    codes = "|".join(SUPPORTED_SOURCE_LANGUAGES)
    match = re.search(rf"(?:[._-])({codes})(?:-orig)?$", path.stem.casefold())
    return match.group(1) if match else None


def discover_best_subtitle(
    directory: str | Path,
    *,
    requested_language: str,
    stem_prefix: str | None = None,
) -> SubtitleSelection | None:
    """Prefer a valid ready SRT and verify its language before running Whisper."""
    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        return None
    candidates = [
        item
        for item in root.glob("*.srt")
        if item.is_file()
        and not item.name.startswith("._")
        and not item.name.endswith(".partial")
        and "turkce" not in item.name.casefold()
        and (
            stem_prefix is None
            or item.stem == stem_prefix
            or item.stem.startswith(f"{stem_prefix}.")
            or item.stem.startswith(f"{stem_prefix}-")
        )
    ]
    candidates.sort(
        key=lambda item: (
            "-orig" not in item.stem.casefold(),
            _filename_language(item) is None,
            item.name.casefold(),
        )
    )
    for candidate in candidates:
        filename_language = _filename_language(candidate)
        if (
            requested_language != AUTO_LANGUAGE
            and filename_language is not None
            and filename_language != requested_language
        ):
            continue
        try:
            cues = clean_rolling_captions(read_srt(candidate))
        except (OSError, ValueError):
            continue
        detected_language: str | None = None
        try:
            from ksi_local.language_detection import detect_text_language

            detected_language = detect_text_language(transcript_text(cues)).code
        except RuntimeError:
            detected_language = filename_language
        if detected_language not in SUPPORTED_SOURCE_LANGUAGES:
            continue
        if requested_language != AUTO_LANGUAGE and detected_language != requested_language:
            continue
        return SubtitleSelection(
            path=candidate,
            detected_language=detected_language,
            reason="Hazır kaynak altyazısı bulundu; Whisper çalıştırılmayacak.",
        )
    return None


def transcript_text(cues: list[Cue]) -> str:
    """Produce readable text while removing exact adjacent caption repetition."""
    paragraphs: list[str] = []
    previous = ""
    for cue in cues:
        text = re.sub(r"<[^>]+>", "", cue.text)
        text = re.sub(r"\s+", " ", text).strip()
        if text and text != previous:
            paragraphs.append(text)
            previous = text
    return "\n".join(paragraphs)


def _milliseconds(value: str) -> int:
    hours, minutes, rest = value.split(":")
    seconds, millis = rest.split(",")
    return (
        int(hours) * 3_600_000
        + int(minutes) * 60_000
        + int(seconds) * 1000
        + int(millis)
    )


def clean_rolling_captions(cues: list[Cue]) -> list[Cue]:
    """Collapse YouTube's rolling two-line captions and 10 ms duplicate cues."""
    if not cues:
        return []
    tiny_ratio = (
        sum(_milliseconds(cue.end) - _milliseconds(cue.start) <= 50 for cue in cues)
        / len(cues)
    )
    if tiny_ratio < 0.2:
        return cues

    cleaned: list[Cue] = []
    previous_lines: list[str] = []
    for cue in cues:
        if _milliseconds(cue.end) - _milliseconds(cue.start) <= 50:
            continue
        current_lines = [line.strip() for line in cue.text.splitlines() if line.strip()]
        overlap = 0
        max_overlap = min(len(previous_lines), len(current_lines))
        for size in range(max_overlap, 0, -1):
            if previous_lines[-size:] == current_lines[:size]:
                overlap = size
                break
        new_lines = current_lines[overlap:]
        if new_lines:
            cleaned.append(cue.with_text("\n".join(new_lines)))
        previous_lines = current_lines
    return [cue.with_text(cue.text) for cue in cleaned]


def timestamped_transcript(cues: list[Cue], *, interval_seconds: int = 30) -> str:
    """Return readable transcript lines with regular traceable source timestamps."""
    lines: list[str] = []
    previous = ""
    last_stamp = -interval_seconds
    for cue in cues:
        text = re.sub(r"<[^>]+>", "", cue.text)
        text = re.sub(r"\s+", " ", text).strip()
        if not text or text == previous:
            continue
        seconds = _milliseconds(cue.start) // 1000
        stamp = f"[{cue.start[:8]}] " if seconds - last_stamp >= interval_seconds else ""
        if stamp:
            last_stamp = seconds
        lines.append(stamp + text)
        previous = text
    return "\n".join(lines)
