"""Original FFmpeg media workflows, shared by desktop, CLI and core clients."""

from __future__ import annotations

import math
import json
import os
import subprocess
import tempfile
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from ksi_local.bundle_runtime import tool_path
from ksi_local.engine_runner import OperationCancelled, run_engine
from ksi_local.media import MAX_DURATION_SECONDS, probe_local_media
from ksi_local.subtitles import read_srt


FORMATS = {
    "mp4": ("libx264", "aac"), "mov": ("libx264", "aac"),
    "mkv": ("libx264", "aac"), "webm": ("libvpx-vp9", "libopus"),
    "mp3": (None, "libmp3lame"), "wav": (None, "pcm_s16le"),
    "flac": (None, "flac"), "aac": (None, "aac"), "gif": ("gif", None),
}
PROFILES = {"share": (23, 1080), "small": (28, 720), "archive": (18, None)}
INPUT_SUFFIXES = {".mp4", ".m4v", ".mkv", ".mov", ".webm", ".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg", ".opus", ".avi"}


@dataclass(frozen=True)
class MediaRequest:
    sources: tuple[str, ...]
    destination: str
    operation: str = "convert"
    profile: str = "share"
    start: float = 0
    end: float | None = None
    lossless: bool = False
    hardware: bool = False
    subtitle: str | None = None
    strip_metadata: bool = False


@dataclass(frozen=True)
class MediaResult:
    output: str
    source_bytes: int
    output_bytes: int
    duration_seconds: float
    stream_copy: bool
    warnings: tuple[str, ...]

    def to_dict(self) -> dict:
        return asdict(self)


def _source(value: str) -> Path:
    raw = Path(value).expanduser()
    if raw.is_symlink() or not raw.is_file():
        raise ValueError("Kaynak symlink olmayan normal bir medya dosyası olmalıdır.")
    path = raw.resolve()
    if any(character in str(path) for character in ("\x00", "\r", "\n")):
        raise ValueError("Medya dosyası yolunda denetim karakteri olamaz.")
    return path


def _validate(request: MediaRequest):
    if request.operation not in {"convert", "trim", "join", "remux", "burn_subtitle"}:
        raise ValueError("Medya işlemi desteklenmiyor.")
    if request.profile not in PROFILES:
        raise ValueError("Medya profili desteklenmiyor.")
    if not request.sources or len(request.sources) > 100:
        raise ValueError("Medya girdisi sayısı 1–100 olmalıdır.")
    if request.operation != "join" and len(request.sources) != 1:
        raise ValueError("Bu işlem tek medya dosyası kullanır; toplu işler sırayla çalıştırılır.")
    if request.operation == "join" and len(request.sources) < 2:
        raise ValueError("Birleştirmek için en az iki medya dosyası gerekir.")
    sources = tuple(_source(value) for value in request.sources)
    if any(source.suffix.lower() not in INPUT_SUFFIXES for source in sources):
        raise ValueError("Girdi biçimi desteklenen yerel medya dosyalarından biri olmalıdır.")
    raw_output = Path(request.destination).expanduser()
    if not raw_output.is_absolute() or raw_output.is_symlink():
        raise ValueError("Çıktı yolu mutlak ve symlink olmayan bir yol olmalıdır.")
    output = raw_output.resolve()
    if output in sources or output.exists():
        raise FileExistsError("Kaynak veya mevcut çıktı dosyasına yazılmaz.")
    fmt = output.suffix.lower().removeprefix(".")
    if fmt not in FORMATS:
        raise ValueError("Çıktı biçimi desteklenmiyor.")
    if not math.isfinite(request.start) or request.start < 0:
        raise ValueError("Başlangıç zamanı geçersiz.")
    if request.end is not None and (not math.isfinite(request.end) or request.end <= request.start):
        raise ValueError("Bitiş zamanı başlangıçtan büyük olmalıdır.")
    if request.lossless and request.operation not in {"trim", "join", "remux"}:
        raise ValueError("Kayıpsız mod yalnız kesme, birleştirme veya remux için kullanılır.")
    if request.lossless and fmt not in {"mp4", "mov", "mkv", "webm"}:
        raise ValueError("Kayıpsız medya işlemi bu çıktı kabında desteklenmiyor.")
    if request.operation == "remux" and not request.lossless:
        raise ValueError("Remux yeniden kodlamaz; kayıpsız mod seçilmelidir.")
    return sources, output, fmt


def _encode_arguments(fmt: str, profile: str, *, hardware: bool):
    video, audio = FORMATS[fmt]
    quality, max_height = PROFILES[profile]
    args: list[str] = []
    if video:
        if fmt == "gif":
            return ["-an", "-vf", "fps=12,scale='min(720,iw)':-2:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse", "-loop", "0"]
        args += ["-map", "0:v:0", "-map", "0:a?", "-sn", "-c:v", "h264_videotoolbox" if hardware and video == "libx264" else video]
        if hardware and video == "libx264":
            args += ["-b:v", "6000k" if profile == "archive" else "3000k", "-allow_sw", "1"]
        else:
            args += ["-crf", str(quality)]
            if video == "libx264":
                args += ["-preset", "medium"]
            elif video == "libvpx-vp9":
                args += ["-b:v", "0"]
        if max_height:
            args += ["-vf", f"scale=-2:'trunc(min({max_height},ih)/2)*2':flags=lanczos"]
        else:
            args += ["-vf", "scale='trunc(iw/2)*2':'trunc(ih/2)*2'"]
        args += ["-pix_fmt", "yuv420p"]
    else:
        args += ["-vn", "-sn", "-map", "0:a:0"]
    if audio:
        args += ["-c:a", audio]
        if audio in {"aac", "libmp3lame", "libopus"}:
            args += ["-b:a", "192k"]
    if fmt in {"mp4", "mov"}:
        args += ["-movflags", "+faststart"]
    return args


def _compatible(information: list[dict]) -> bool:
    keys = ("video_stream_count", "audio_stream_count", "video_codecs", "audio_codecs", "dimensions")
    return all(all(info.get(key) == information[0].get(key) for key in keys) for info in information[1:])


def _stream_signature(path: Path, ffprobe: str) -> list[dict]:
    result = subprocess.run(
        [ffprobe, "-v", "error", "-protocol_whitelist", "file,pipe", "-show_entries",
         "stream=codec_type,codec_name,profile,pix_fmt,width,height,sample_aspect_ratio,r_frame_rate,time_base,sample_rate,channels,channel_layout,extradata_size",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=False, timeout=30,
    )
    if result.returncode or len(result.stdout) > 1024 * 1024:
        raise RuntimeError("Birleştirme iz uyumluluğu doğrulanamadı.")
    data = json.loads(result.stdout)
    return [row for row in data.get("streams", []) if row.get("codec_type") in {"video", "audio"}]


def process_media(
    request: MediaRequest, *, cancel: threading.Event | None = None,
    on_progress: Callable[[float], None] | None = None,
) -> MediaResult:
    sources, output, fmt = _validate(request)
    ffmpeg = str(tool_path("ffmpeg"))
    ffprobe = str(tool_path("ffprobe"))
    information = [probe_local_media(str(path), ffprobe_path=ffprobe) for path in sources]
    durations = [float(info.get("duration_seconds") or 0) for info in information]
    if any(not math.isfinite(duration) or duration <= 0 for duration in durations):
        raise ValueError("Medya süresi doğrulanamadı.")
    total = sum(durations)
    if total > MAX_DURATION_SECONDS:
        raise ValueError("Medya toplam süresi güvenli işlem sınırını aşıyor.")
    if request.operation == "trim" and (request.start >= durations[0] or (request.end is not None and request.end > durations[0] + 0.05)):
        raise ValueError("Kesme aralığı medya süresinin dışında.")
    if request.operation == "join" and not _compatible(information):
        raise ValueError("Medya parçalarının codec, görüntü veya iz yapısı uyumsuz; önce aynı profile dönüştürün.")
    if request.operation == "join":
        signatures = [_stream_signature(path, ffprobe) for path in sources]
        if any(signature != signatures[0] for signature in signatures[1:]):
            raise ValueError("Parçaların ses, kare hızı veya zaman tabanı uyumsuz; önce aynı profile dönüştürün.")
    if request.operation == "burn_subtitle" and (fmt not in {"mp4", "mov", "mkv", "webm"} or not request.subtitle):
        raise ValueError("Kalıcı altyazı için altyazı dosyası ve video biçimi gerekir.")
    output.parent.mkdir(parents=True, exist_ok=True)
    expected = (request.end or durations[0]) - request.start if request.operation == "trim" else total
    warnings: list[str] = []
    if request.operation == "trim" and request.lossless:
        warnings.append("Kayıpsız kesme anahtar karelere bağlıdır; başlangıç kare-hassas olmayabilir.")
    if fmt in {"mp3", "aac", "gif"} or not request.lossless:
        warnings.append("Bu işlem yeniden kodlar; çıktı kalitesi/biçimi kaynakla aynı olmayabilir.")
    with tempfile.TemporaryDirectory(prefix=".ksi-media-", dir=output.parent) as staging:
        stage = Path(staging)
        temporary = stage / f"result.{fmt}"
        args = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "warning", "-y", "-protocol_whitelist", "file,pipe"]
        if request.operation == "join":
            records = "\n".join("file '" + str(path).replace("'", "'\\''") + "'" for path in sources)
            (stage / "inputs.ffconcat").write_text(records + "\n", encoding="utf-8")
            args += ["-f", "concat", "-safe", "0", "-i", str(stage / "inputs.ffconcat")]
        else:
            if request.operation == "trim" and request.lossless:
                args += ["-ss", str(request.start)]
            args += ["-i", str(sources[0])]
            if request.operation == "trim" and not request.lossless:
                args += ["-ss", str(request.start)]
        if request.operation == "trim":
            args += ["-t", str(expected), "-avoid_negative_ts", "make_zero"]
        if request.lossless:
            args += ["-map", "0:v?", "-map", "0:a?", "-c", "copy"]
        else:
            args += _encode_arguments(fmt, request.profile, hardware=request.hardware)
        if request.operation == "burn_subtitle":
            subtitle = _source(str(request.subtitle))
            if subtitle.suffix.lower() != ".srt" or not read_srt(subtitle):
                raise ValueError("Altyazı UTF-8 SRT biçiminde ve boş olmayan bir dosya olmalıdır.")
            (stage / "captions.srt").write_bytes(subtitle.read_bytes())
            # Fixed safe basename avoids libavfilter filename escaping/injection.
            scale = ""
            if "-vf" in args:
                index = args.index("-vf")
                scale = args[index + 1] + ","
                del args[index:index + 2]
            args += ["-vf", scale + "subtitles=filename=captions.srt"]
        if request.strip_metadata:
            args += ["-map_metadata", "-1", "-map_chapters", "-1"]
        args += ["-progress", "pipe:1", "-nostats", str(temporary)]

        def progress(line: str):
            if on_progress and line.startswith("out_time_us="):
                try:
                    fraction = float(line.partition("=")[2]) / (expected * 1_000_000)
                    on_progress(min(0.99, max(0.0, fraction)))
                except ValueError:
                    pass

        timeout = min(24 * 3600, max(600, expected * 10))
        try:
            run_engine(args, cwd=stage, timeout=timeout, cancel=cancel, on_line=progress)
        except RuntimeError as error:
            if isinstance(error, OperationCancelled) or "h264_videotoolbox" not in args or "videotoolbox" not in str(error).casefold():
                raise
            args[args.index("h264_videotoolbox")] = "libx264"
            for option in ("-b:v", "-allow_sw"):
                index = args.index(option)
                del args[index:index + 2]
            args[-1:-1] = ["-crf", str(PROFILES[request.profile][0]), "-preset", "medium"]
            warnings.append("Donanım kodlayıcı kullanılamadı; yazılım kodlayıcıya geçildi.")
            run_engine(args, cwd=stage, timeout=timeout, cancel=cancel, on_line=progress)
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise RuntimeError("Medya motoru geçerli çıktı üretmedi.")
        result_info = probe_local_media(str(temporary), ffprobe_path=ffprobe)
        result_duration = float(result_info.get("duration_seconds") or 0)
        if result_duration <= 0 or not math.isfinite(result_duration):
            raise RuntimeError("Çıktı süresi doğrulanamadı.")
        if not request.lossless and abs(result_duration - expected) > max(2.0, expected * 0.02):
            raise RuntimeError("Yeniden kodlanan çıktı süresi beklenen aralıkla eşleşmiyor.")
        if cancel is not None and cancel.is_set():
            raise OperationCancelled("İşlem iptal edildi; çıktı yayımlanmadı.")
        # Publish without overwriting a file created while the job was running.
        os.link(temporary, output)
        if on_progress:
            on_progress(1.0)
        return MediaResult(str(output), sum(path.stat().st_size for path in sources), output.stat().st_size, result_duration, request.lossless, tuple(warnings))
