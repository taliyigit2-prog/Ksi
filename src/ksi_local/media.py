"""Local media validation using ffprobe without loading media into memory."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any
from ksi_local.bundle_runtime import tool_path


MAX_DURATION_SECONDS = 3 * 60 * 60
MEDIA_SUFFIXES = {".mp4", ".mkv", ".mov", ".webm", ".m4v"}
AUDIO_SUFFIXES = {".m4a", ".mp3", ".opus", ".ogg", ".wav", ".aac"}
PROCESSING_MEDIA_SUFFIXES = MEDIA_SUFFIXES | AUDIO_SUFFIXES


def summarize_ffprobe(payload: dict[str, Any]) -> dict[str, Any]:
    format_info = payload.get("format")
    if not isinstance(format_info, dict):
        format_info = {}
    streams = [stream for stream in payload.get("streams") or [] if isinstance(stream, dict)]

    duration_value = format_info.get("duration")
    size_value = format_info.get("size")
    try:
        duration = float(duration_value) if duration_value is not None else None
    except (TypeError, ValueError):
        duration = None
    try:
        size = int(size_value) if size_value is not None else None
    except (TypeError, ValueError):
        size = None

    video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    dimensions = sorted(
        {
            (int(stream["width"]), int(stream["height"]))
            for stream in video_streams
            if isinstance(stream.get("width"), int) and isinstance(stream.get("height"), int)
        }
    )
    return {
        "duration_seconds": duration,
        "size_bytes": size,
        "format_names": str(format_info.get("format_name") or "").split(","),
        "video_stream_count": len(video_streams),
        "audio_stream_count": len(audio_streams),
        "video_codecs": sorted(
            {str(stream["codec_name"]) for stream in video_streams if stream.get("codec_name")}
        ),
        "audio_codecs": sorted(
            {str(stream["codec_name"]) for stream in audio_streams if stream.get("codec_name")}
        ),
        "dimensions": [list(item) for item in dimensions],
        "within_mvp_duration": duration is not None and 0 < duration <= MAX_DURATION_SECONDS,
    }


def probe_local_media(raw_path: str, *, ffprobe_path: str | None = None) -> dict[str, Any]:
    path = Path(raw_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError("Yerel medya dosyası bulunamadı.")
    if not path.is_file():
        raise ValueError("Seçilen yol normal bir dosya değil.")

    executable = ffprobe_path or tool_path("ffprobe", required=False)
    if executable is None:
        raise RuntimeError("ffprobe bulunamadı.")

    completed = subprocess.run(
        [
            executable,
            "-v",
            "error",
            "-show_entries",
            (
                "format=duration,size,format_name:"
                "stream=index,codec_type,codec_name,width,height,sample_rate,channels"
            ),
            "-of",
            "json",
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        shell=False,
    )
    if completed.returncode != 0:
        last_line = next(
            (line.strip() for line in reversed(completed.stderr.splitlines()) if line.strip()),
            "ffprobe medya dosyasını okuyamadı.",
        )
        raise RuntimeError(last_line[:500])
    payload = json.loads(completed.stdout)
    if not isinstance(payload, dict):
        raise RuntimeError("ffprobe beklenen JSON nesnesini döndürmedi.")
    return summarize_ffprobe(payload)


def sha256_file(path: str | Path, *, chunk_size: int = 8 * 1024 * 1024) -> str:
    target = Path(path).expanduser().resolve()
    digest = hashlib.sha256()
    with target.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def verify_media_file(
    raw_path: str | Path,
    *,
    ffprobe_path: str | None = None,
    require_audio: bool = True,
) -> dict[str, Any]:
    """Reject incomplete media and return a compact integrity record."""
    path = Path(raw_path).expanduser().resolve()
    probe = probe_local_media(str(path), ffprobe_path=ffprobe_path)
    if path.suffix.casefold() not in MEDIA_SUFFIXES:
        raise ValueError("Desteklenmeyen medya dosyası uzantısı.")
    if probe["video_stream_count"] < 1:
        raise RuntimeError("Dosyada doğrulanabilir bir video akışı bulunamadı.")
    if require_audio and probe["audio_stream_count"] < 1:
        raise RuntimeError("Dosyada doğrulanabilir bir ses akışı bulunamadı.")
    if not probe["within_mvp_duration"]:
        raise RuntimeError("Video süresi sıfırdan büyük ve en fazla üç saat olmalıdır.")
    actual_size = path.stat().st_size
    reported_size = probe.get("size_bytes")
    if isinstance(reported_size, int) and reported_size != actual_size:
        raise RuntimeError("Dosya boyutu ffprobe sonucu ile eşleşmiyor.")
    return {
        "filename": path.name,
        "size_bytes": actual_size,
        "sha256": sha256_file(path),
        "media": probe,
    }


def verify_audio_file(
    raw_path: str | Path, *, ffprobe_path: str | None = None
) -> dict[str, Any]:
    """Verify a summary-only audio download without requiring a video stream."""
    path = Path(raw_path).expanduser().resolve()
    probe = probe_local_media(str(path), ffprobe_path=ffprobe_path)
    if path.suffix.casefold() not in AUDIO_SUFFIXES:
        raise ValueError("Desteklenmeyen ses dosyası uzantısı.")
    if probe["audio_stream_count"] < 1:
        raise RuntimeError("Dosyada doğrulanabilir bir ses akışı bulunamadı.")
    if not probe["within_mvp_duration"]:
        raise RuntimeError("Ses süresi sıfırdan büyük ve en fazla üç saat olmalıdır.")
    actual_size = path.stat().st_size
    reported_size = probe.get("size_bytes")
    if isinstance(reported_size, int) and reported_size != actual_size:
        raise RuntimeError("Ses dosyası boyutu ffprobe sonucu ile eşleşmiyor.")
    return {
        "filename": path.name,
        "size_bytes": actual_size,
        "sha256": sha256_file(path),
        "media": probe,
    }
