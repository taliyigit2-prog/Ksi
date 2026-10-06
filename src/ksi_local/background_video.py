"""Bounded short-video background removal with one offline model worker."""

import json
import math
import os
import shutil
import sys
import tempfile
from pathlib import Path

from ksi_local.atomic_files import atomic_write_json
from ksi_local.bundle_runtime import OfflinePayload, bundle_root, tool_path
from ksi_local.engine_runner import OperationCancelled, run_engine
from ksi_local.media import LOCAL_FORMAT_WHITELIST, probe_local_media


def process_background_video(request, *, cancel=None, on_progress=None):
    from ksi_local.media_tools import MediaResult, _stream_signature, _validate

    sources, output, fmt = _validate(request)
    if fmt not in {"mov", "mp4"}:
        raise ValueError("Arka plan videosu alpha MOV veya siyah arka planlı MP4 olabilir.")
    source = sources[0]
    info = probe_local_media(str(source))
    duration = float(info.get("duration_seconds") or 0)
    if not math.isfinite(duration) or not 0 < duration <= 30 or not info.get("video_stream_count"):
        raise ValueError("AI video arka plan işlemi en fazla 30 saniyelik video gerektirir.")
    if any(width * height > 33_000_000 for width, height in info.get("dimensions", ())):
        raise ValueError("Video kaynak kare boyutu güvenli sınırı aşıyor.")
    resources = bundle_root()
    if resources is None:
        raise RuntimeError("Video arka plan modeli doğrulanmış çevrimdışı pakette bulunmalıdır.")
    payload = OfflinePayload.load(resources)
    model = payload.component("model", "u2netp")
    model_hash = next(entry.sha256 for entry in payload.files if entry.role == "model" and entry.identifier == "u2netp")
    output.parent.mkdir(parents=True, exist_ok=True)
    # Two bounded PNG sequences and the alpha output can temporarily be large.
    required = math.ceil(duration * 20) * 1280 * 720 * 8 + 512 * 1024**2
    if shutil.disk_usage(output.parent).free < required:
        raise OSError("Kısa video arka plan işlemi için yeterli geçici disk alanı yok.")
    ffmpeg, ffprobe = str(tool_path("ffmpeg")), str(tool_path("ffprobe"))
    with tempfile.TemporaryDirectory(prefix=".ksi-video-mask-", dir=output.parent) as temporary:
        stage = Path(temporary)
        frames, masked = stage / "frames", stage / "masked"
        frames.mkdir(mode=0o700)
        run_engine([ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-threads", "2",
            "-protocol_whitelist", "file,pipe", "-format_whitelist", LOCAL_FORMAT_WHITELIST, "-i", str(source), "-map", "0:v:0", "-an",
            "-vf", "fps=20,scale='min(1280,iw)':'min(720,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2",
            "-frames:v", "600", str(frames / "frame-%06d.png")], timeout=300, cancel=cancel)
        job, result = stage / "request.json", stage / "result.json"
        atomic_write_json(job, {"operation": "remove_background_frames", "source": str(frames),
            "destination": str(masked), "model": str(model), "model_sha256": model_hash})

        def progress(line):
            if on_progress and line.startswith("KSI_FRAME_PROGRESS="):
                current, total = line.partition("=")[2].split("/")
                on_progress(0.05 + 0.8 * int(current) / int(total))

        run_engine([sys.executable, "-m", "ksi_local.local_ai_worker", str(job), str(result)],
                   timeout=1800, cancel=cancel, on_line=progress)
        if not result.is_file() or result.stat().st_size > 65536:
            raise RuntimeError("Video maskesi işçi yanıtı geçersiz.")
        data = json.loads(result.read_text(encoding="utf-8"))
        if not data.get("ok") or type(data.get("frames")) is not int or not 1 <= data["frames"] <= 600:
            raise RuntimeError("Video maskesi sonucu doğrulanamadı.")
        candidate = stage / ("result." + fmt)
        args = [ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-threads", "2",
            "-protocol_whitelist", "file,pipe", "-format_whitelist", "image2", "-framerate", "20", "-i", str(masked / "frame-%06d.png"), "-format_whitelist", LOCAL_FORMAT_WHITELIST, "-i", str(source)]
        if fmt == "mov":
            args += ["-map", "0:v:0", "-map", "1:a?", "-c:v", "prores_ks", "-profile:v", "4",
                     "-pix_fmt", "yuva444p10le", "-alpha_bits", "16"]
        else:
            width, height = data["width"], data["height"]
            if type(width) is not int or type(height) is not int or not 2 <= width <= 1280 or not 2 <= height <= 720:
                raise ValueError("Video maskesi boyutları geçersiz.")
            args += ["-f", "lavfi", "-format_whitelist", "lavfi", "-i", f"color=c=black:s={width}x{height}:r=20",
                "-filter_complex", "[2:v][0:v]overlay=shortest=1:format=auto,format=yuv420p[v]",
                "-map", "[v]", "-map", "1:a?", "-c:v", "libx264", "-crf", "23", "-preset", "medium", "-movflags", "+faststart"]
        args += ["-c:a", "aac", "-t", str(duration)]
        if request.strip_metadata:
            args += ["-map_metadata", "-1", "-map_chapters", "-1"]
        args += [str(candidate)]
        run_engine(args, timeout=600, cancel=cancel)
        verified = probe_local_media(str(candidate), ffprobe_path=ffprobe)
        result_duration = float(verified.get("duration_seconds") or 0)
        if not verified.get("video_stream_count") or not math.isfinite(result_duration) or result_duration <= 0 or abs(result_duration - duration) > 0.15:
            raise RuntimeError("Arka plan videosu süre/iz doğrulamasından geçmedi.")
        if fmt == "mov" and not any(str(stream.get("pix_fmt", "")).startswith("yuva") for stream in _stream_signature(candidate, ffprobe)):
            raise RuntimeError("MOV çıktısında alpha kanalı doğrulanamadı.")
        if cancel is not None and cancel.is_set():
            raise OperationCancelled("Video arka plan işlemi iptal edildi.")
        os.link(candidate, output)
        if on_progress:
            on_progress(1)
        return MediaResult(str(output), source.stat().st_size, output.stat().st_size,
            result_duration, False, ("AI video: en fazla 30 sn, 20 fps, 1280×720; MOV alpha, MP4 siyah arka plan.",))
