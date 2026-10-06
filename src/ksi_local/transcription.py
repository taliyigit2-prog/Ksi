"""Apple-Silicon transcription adapter loaded only inside its worker process."""

from __future__ import annotations

import gc
from pathlib import Path
from typing import Any

from ksi_local.languages import AUTO_LANGUAGE, SUPPORTED_SOURCE_LANGUAGES
from ksi_local.media import probe_local_media
from ksi_local.subtitles import Cue, write_srt


DEFAULT_WHISPER_MODEL = "mlx-community/whisper-large-v3-turbo-8bit"
NO_SPEECH_THRESHOLD = 0.6
HALLUCINATION_SILENCE_SECONDS = 2.0


def _srt_time(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def segments_to_cues(
    segments: list[dict[str, Any]], *, duration_seconds: float | None = None
) -> list[Cue]:
    cues: list[Cue] = []
    for segment in segments:
        try:
            start = float(segment["start"])
            end = float(segment["end"])
            text = str(segment["text"]).strip()
        except (KeyError, TypeError, ValueError):
            continue
        if duration_seconds is not None:
            if start >= duration_seconds:
                continue
            end = min(end, duration_seconds)
        if text and end > start >= 0:
            cues.append(Cue(len(cues) + 1, _srt_time(start), _srt_time(end), text))
    if not cues:
        raise RuntimeError("Whisper geçerli konuşma segmenti üretmedi.")
    return cues


def filter_speech_segments(
    segments: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Drop high-confidence silence and extreme low-confidence repetitions."""
    kept: list[dict[str, Any]] = []
    dropped = 0
    for segment in segments:
        no_speech = segment.get("no_speech_prob")
        log_probability = segment.get("avg_logprob")
        compression_ratio = segment.get("compression_ratio")
        silent = (
            isinstance(no_speech, (int, float))
            and isinstance(log_probability, (int, float))
            and float(no_speech) >= 0.8
            and float(log_probability) <= -0.5
        )
        repetitive = (
            isinstance(compression_ratio, (int, float))
            and isinstance(log_probability, (int, float))
            and float(compression_ratio) > 3.0
            and float(log_probability) <= -0.8
        )
        if silent or repetitive:
            dropped += 1
        else:
            kept.append(segment)
    return kept, dropped


def transcribe_media(
    input_path: str | Path,
    output_srt: str | Path,
    *,
    language: str,
    model: str = DEFAULT_WHISPER_MODEL,
    initial_prompt: str | None = None,
) -> dict[str, object]:
    if language != AUTO_LANGUAGE and language not in {
        *SUPPORTED_SOURCE_LANGUAGES,
        "tr",
    }:
        raise ValueError("Seçilen Whisper kaynak dili desteklenmiyor.")
    source = Path(input_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError("Ses yazımı için medya dosyası bulunamadı.")
    if initial_prompt is not None:
        initial_prompt = " ".join(initial_prompt.split()).strip()[:500] or None
    media_info = probe_local_media(str(source))
    duration = media_info.get("duration_seconds")
    duration_seconds = float(duration) if isinstance(duration, (int, float)) else None
    try:
        import mlx_whisper
    except ImportError as error:
        raise RuntimeError("mlx-whisper kurulu değil.") from error
    result = mlx_whisper.transcribe(
        str(source),
        path_or_hf_repo=model,
        language=None if language == AUTO_LANGUAGE else language,
        word_timestamps=True,
        condition_on_previous_text=True,
        temperature=0.0,
        compression_ratio_threshold=2.4,
        logprob_threshold=-1.0,
        no_speech_threshold=NO_SPEECH_THRESHOLD,
        hallucination_silence_threshold=HALLUCINATION_SILENCE_SECONDS,
        initial_prompt=initial_prompt,
        verbose=None,
    )
    segments = result.get("segments") if isinstance(result, dict) else None
    if not isinstance(segments, list):
        raise RuntimeError("Whisper beklenen segment listesini döndürmedi.")
    detected = result.get("language") if isinstance(result, dict) else None
    detected_code = str(detected).lower().split("-")[0] if detected else language
    if language == AUTO_LANGUAGE and detected_code not in SUPPORTED_SOURCE_LANGUAGES:
        raise RuntimeError(
            f"Algılanan kaynak dili ({detected_code}) bu sürümde desteklenmiyor. "
            "Kaynak dili elle seçin."
        )
    speech_segments, dropped_segments = filter_speech_segments(
        [item for item in segments if isinstance(item, dict)]
    )
    cues = segments_to_cues(
        speech_segments,
        duration_seconds=duration_seconds,
    )
    write_srt(output_srt, cues)
    del result
    gc.collect()
    try:
        import mlx.core as mx

        mx.clear_cache()
    except (ImportError, AttributeError):
        pass
    return {
        "segments": len(cues),
        "silence_or_hallucination_segments_dropped": dropped_segments,
        "detected_language": detected_code,
        "model": model,
        "no_speech_threshold": NO_SPEECH_THRESHOLD,
        "hallucination_silence_seconds": HALLUCINATION_SILENCE_SECONDS,
    }
