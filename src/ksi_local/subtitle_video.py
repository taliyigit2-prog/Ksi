"""Create and verify a final video containing the translated subtitle track."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from ksi_local.atomic_files import atomic_replace
from ksi_local.media import probe_local_media, verify_media_file
from ksi_local.subtitles import read_srt


MP4_COPY_SUFFIXES = {".mp4", ".m4v"}


def subtitled_output_path(source_video: str | Path, output_directory: str | Path) -> Path:
    """Choose a stream-copy-friendly final container for the source video."""
    source = Path(source_video)
    suffix = ".mp4" if source.suffix.casefold() in MP4_COPY_SUFFIXES else ".mkv"
    return Path(output_directory) / f"turkce-altyazili{suffix}"


def _probe_subtitle_streams(path: Path, *, ffprobe_path: str) -> list[dict[str, Any]]:
    completed = subprocess.run(
        [
            ffprobe_path,
            "-v",
            "error",
            "-select_streams",
            "s",
            "-show_entries",
            "stream=index,codec_type,codec_name:stream_tags=language,title:stream_disposition=default",
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
        raise RuntimeError("FFprobe final videodaki altyazı kanalını doğrulayamadı.")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("FFprobe altyazı doğrulaması geçersiz JSON döndürdü.") from error
    streams = payload.get("streams") if isinstance(payload, dict) else None
    return [item for item in streams or [] if isinstance(item, dict)]


def mux_subtitled_video(
    source_video: str | Path,
    translated_subtitle: str | Path,
    destination: str | Path,
    *,
    ffmpeg_path: str,
    ffprobe_path: str,
) -> dict[str, Any]:
    """Embed Turkish subtitles as the default selectable track without re-encoding AV."""
    source = Path(source_video).expanduser().resolve()
    subtitle = Path(translated_subtitle).expanduser().resolve()
    output = Path(destination).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError("Altyazılı video için kaynak video bulunamadı.")
    if not subtitle.is_file() or not read_srt(subtitle):
        raise ValueError("Videoya eklenecek Türkçe altyazı boş veya geçersiz.")
    if output.suffix.casefold() not in {".mp4", ".mkv"}:
        raise ValueError("Altyazılı video çıktısı MP4 veya MKV olmalıdır.")
    output.parent.mkdir(parents=True, exist_ok=True)

    source_info = probe_local_media(str(source), ffprobe_path=ffprobe_path)
    if int(source_info.get("video_stream_count") or 0) < 1:
        raise RuntimeError("Kaynak dosyada doğrulanabilir video akışı yok.")
    if int(source_info.get("audio_stream_count") or 0) < 1:
        raise RuntimeError("Kaynak dosyada doğrulanabilir ses akışı yok.")

    temporary = output.with_name(f".{output.stem}.part{output.suffix.casefold()}")
    subtitle_codec = "mov_text" if output.suffix.casefold() == ".mp4" else "srt"
    argv = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-i",
        str(subtitle),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-map",
        "1:0",
        "-map_metadata",
        "0",
        "-map_chapters",
        "0",
        "-c:v",
        "copy",
        "-c:a",
        "copy",
        "-c:s",
        subtitle_codec,
        "-metadata:s:s:0",
        "language=tur",
        "-metadata:s:s:0",
        "title=Türkçe",
        "-disposition:s:0",
        "default",
    ]
    if output.suffix.casefold() == ".mp4":
        argv.extend(("-movflags", "+faststart"))
    argv.append(str(temporary))

    completed = subprocess.run(
        argv,
        check=False,
        capture_output=True,
        text=True,
        timeout=4 * 60 * 60,
        shell=False,
    )
    if completed.returncode != 0:
        temporary.unlink(missing_ok=True)
        last_line = next(
            (line.strip() for line in reversed(completed.stderr.splitlines()) if line.strip()),
            "FFmpeg Türkçe altyazıyı videoya ekleyemedi.",
        )
        raise RuntimeError(last_line[:500])

    try:
        verification = verify_media_file(
            temporary, ffprobe_path=ffprobe_path, require_audio=True
        )
        subtitle_streams = _probe_subtitle_streams(
            temporary, ffprobe_path=ffprobe_path
        )
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    turkish_default = [
        stream
        for stream in subtitle_streams
        if str((stream.get("tags") or {}).get("language") or "").casefold() == "tur"
        and int((stream.get("disposition") or {}).get("default") or 0) == 1
    ]
    if len(turkish_default) != 1:
        temporary.unlink(missing_ok=True)
        raise RuntimeError("Final videoda varsayılan Türkçe altyazı kanalı doğrulanamadı.")
    expected_codec = "mov_text" if output.suffix.casefold() == ".mp4" else "subrip"
    if str(turkish_default[0].get("codec_name") or "") != expected_codec:
        temporary.unlink(missing_ok=True)
        raise RuntimeError("Final videodaki Türkçe altyazı biçimi doğrulanamadı.")

    source_duration = source_info.get("duration_seconds")
    output_duration = verification.get("media", {}).get("duration_seconds")
    if isinstance(source_duration, float) and isinstance(output_duration, float):
        tolerance = max(1.0, source_duration * 0.002)
        if abs(source_duration - output_duration) > tolerance:
            temporary.unlink(missing_ok=True)
            raise RuntimeError("Altyazılı videonun süresi kaynak videoyla eşleşmiyor.")

    atomic_replace(temporary, output)
    verification["subtitle"] = {
        "stream_count": len(subtitle_streams),
        "language": "tur",
        "title": "Türkçe",
        "default": True,
        "codec": expected_codec,
    }
    return verification
