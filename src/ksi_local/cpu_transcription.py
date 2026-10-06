"""Offline whisper.cpp adapter for Intel macOS and explicit CPU processing."""

from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path

from ksi_local.bundle_runtime import tool_path
from ksi_local.engine_runner import run_engine
from ksi_local.subtitles import write_srt


def transcribe_cpu(
    source: Path, output: Path, *, model: str, language: str,
    duration_seconds: float | None, initial_prompt: str | None,
) -> dict:
    from ksi_local.transcription import segments_to_cues

    raw_model = Path(model).expanduser()
    if raw_model.is_symlink() or not raw_model.is_file() or raw_model.suffix != ".bin":
        raise RuntimeError("Intel konuşma yazımı için paketteki yerel Whisper GGML modeli bulunamadı.")
    output.parent.mkdir(parents=True, exist_ok=True)
    if not duration_seconds or not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise ValueError("Konuşma yazımı için geçerli medya süresi gerekir.")
    with tempfile.TemporaryDirectory(prefix=".ksi-whisper-", dir=output.parent) as temporary:
        stage = Path(temporary)
        audio = stage / "input.wav"
        run_engine([
            str(tool_path("ffmpeg")), "-hide_banner", "-nostdin", "-v", "error",
            "-protocol_whitelist", "file,pipe", "-i", str(source), "-vn", "-ar", "16000",
            "-ac", "1", "-c:a", "pcm_s16le", str(audio),
        ], timeout=max(300, duration_seconds * 2))
        result_base = stage / "transcript"
        arguments = [
            str(tool_path("whisper-cli")), "-m", str(raw_model.resolve()), "-f", str(audio),
            "-l", "auto" if language == "auto" else language, "-t", "4", "-ng",
            "-oj", "-of", str(result_base),
        ]
        if initial_prompt:
            arguments += ["--prompt", initial_prompt]
        run_engine(arguments, timeout=min(24 * 3600, max(600, duration_seconds * 10)))
        result_file = result_base.with_suffix(".json")
        if not result_file.is_file() or result_file.stat().st_size > 16 * 1024**2:
            raise RuntimeError("Whisper CPU çıktısı eksik veya boyut sınırını aşıyor.")
        result = json.loads(result_file.read_text(encoding="utf-8"))
        transcription = result.get("transcription")
        if not isinstance(transcription, list) or len(transcription) > 30000:
            raise RuntimeError("Whisper CPU segment listesi geçersiz.")
        segments = []
        for segment in transcription:
            if not isinstance(segment, dict) or not isinstance(segment.get("offsets"), dict):
                continue
            offsets = segment["offsets"]
            try:
                start, end = float(offsets["from"]) / 1000, float(offsets["to"]) / 1000
            except (KeyError, TypeError, ValueError):
                continue
            if math.isfinite(start) and math.isfinite(end):
                segments.append({"start": start, "end": end, "text": str(segment.get("text") or "")})
        cues = segments_to_cues(segments, duration_seconds=duration_seconds)
        write_srt(output, cues)
        detected = result.get("result", {}).get("language", language)
        return {
            "segments": len(cues), "detected_language": detected,
            "model": str(raw_model), "backend": "whisper.cpp-cpu",
            "silence_or_hallucination_segments_dropped": 0,
        }
