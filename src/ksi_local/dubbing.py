"""Deterministic timing, quality, and mux helpers for Turkish dubbing."""

from __future__ import annotations

import json
import math
import re
import statistics
import subprocess
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ksi_local.atomic_files import atomic_replace
from ksi_local.media import probe_local_media, sha256_file, verify_media_file
from ksi_local.subtitles import Cue, transcript_text


NORMAL_SPEED_MIN = 0.90
NORMAL_SPEED_MAX = 1.10
REWRITE_SPEED_MIN = 0.85
REWRITE_SPEED_MAX = 1.18
TARGET_LUFS = -16.0
TARGET_TRUE_PEAK_DBFS = -1.0
MAX_MEDIAN_ONSET_MS = 250.0
MAX_P95_ONSET_MS = 500.0


@dataclass(frozen=True)
class VoiceProfile:
    engine: str
    description: str
    seed: int
    language: str
    exaggeration: float
    cfg_weight: float
    device: str
    model_relative_directory: str
    profile_sha256: str


@dataclass(frozen=True)
class SegmentMetric:
    index: int
    start_seconds: float
    end_seconds: float
    target_seconds: float
    generated_seconds: float
    speed_factor: float
    leading_silence_seconds: float
    peak_dbfs: float | None
    text_sha256: str
    audio_sha256: str
    status: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def timestamp_seconds(value: str) -> float:
    hours, minutes, remainder = value.split(":")
    seconds, milliseconds = remainder.split(",")
    return (
        int(hours) * 3600
        + int(minutes) * 60
        + int(seconds)
        + int(milliseconds) / 1000
    )


def cue_duration(cue: Cue) -> float:
    duration = timestamp_seconds(cue.end) - timestamp_seconds(cue.start)
    if duration <= 0:
        raise ValueError(f"{cue.index}. dublaj segmentinin süresi geçersiz.")
    return duration


def clean_spoken_text(text: str) -> str:
    cleaned = re.sub(r"<[^>]+>", " ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        raise ValueError("Boş altyazı segmenti seslendirilemez.")
    if len(cleaned) > 1000:
        raise ValueError("Tek dublaj segmenti 1000 karakter sınırını aşıyor.")
    return cleaned


def text_sha256(cue: Cue) -> str:
    import hashlib

    payload = json.dumps(
        {
            "index": cue.index,
            "start": cue.start,
            "end": cue.end,
            "text": clean_spoken_text(cue.text),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_voice_profile(path: str | Path, *, bundled_default: bool = False) -> VoiceProfile:
    source = Path(path).expanduser().resolve()
    if not source.is_file() or source.stat().st_size > 128 * 1024:
        raise ValueError("Dublaj ses profili bulunamadı veya çok büyük.")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError("Dublaj ses profili geçerli JSON değil.") from error
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("Dublaj ses profili şeması desteklenmiyor.")
    if bundled_default:
        from ksi_local.bundle_runtime import OfflinePayload, bundle_root

        resources = bundle_root()
        if resources is None or OfflinePayload.load(resources).component("support", "voice-profile").resolve() != source:
            raise ValueError("Yerel varsayılan ses profili doğrulanmış pakete ait değil.")
        if payload.get("status") != "local-default" or payload.get("accepted_at") is not None or "user_evaluation" in payload:
            raise ValueError("Paket ses profili insan onayı iddiası içermeyen yerel varsayılan olmalıdır.")
    elif payload.get("status") != "accepted":
        raise ValueError("Dublaj ses profili kullanıcı tarafından kabul edilmemiş.")
    voice = payload.get("voice")
    model = payload.get("model")
    runtime = payload.get("runtime")
    if not all(isinstance(item, dict) for item in (voice, model, runtime)):
        raise ValueError("Dublaj ses profili alanları eksik.")
    assert isinstance(voice, dict) and isinstance(model, dict) and isinstance(runtime, dict)
    seed = int(voice.get("seed", -1))
    exaggeration = float(voice.get("exaggeration", -1))
    cfg_weight = float(voice.get("cfg_weight", -1))
    language = str(voice.get("language") or "")
    device = str(runtime.get("device") or "")
    relative_model = str(model.get("workspace_relative_directory") or "")
    if seed < 0 or not 0 <= exaggeration <= 2 or not 0 <= cfg_weight <= 1:
        raise ValueError("Dublaj ses profili üretim ayarları geçersiz.")
    if language != "tr" or device not in {"mps", "cpu"}:
        raise ValueError("Dublaj ses profili Türkçe veya desteklenen cihaz için değil.")
    if Path(relative_model).is_absolute() or ".." in Path(relative_model).parts:
        raise ValueError("Dublaj model yolu çalışma alanı içinde olmalıdır.")
    return VoiceProfile(
        engine=str(payload.get("engine") or ""),
        description=str(voice.get("description") or ""),
        seed=seed,
        language=language,
        exaggeration=exaggeration,
        cfg_weight=cfg_weight,
        device=device,
        model_relative_directory=relative_model,
        profile_sha256=sha256_file(source),
    )


def speed_factor(generated_seconds: float, target_seconds: float) -> float:
    if generated_seconds <= 0 or target_seconds <= 0:
        raise ValueError("Dublaj süreleri sıfırdan büyük olmalıdır.")
    return generated_seconds / target_seconds


def speed_status(factor: float) -> str:
    if NORMAL_SPEED_MIN <= factor <= NORMAL_SPEED_MAX:
        return "normal"
    if REWRITE_SPEED_MIN <= factor <= REWRITE_SPEED_MAX:
        return "bounded_adjustment"
    return "needs_rewrite"


def atempo_chain(factor: float) -> str:
    """Return FFmpeg atempo stages for any safe finite positive factor."""
    if not math.isfinite(factor) or factor <= 0:
        raise ValueError("Geçersiz dublaj hız çarpanı.")
    stages: list[float] = []
    remaining = factor
    while remaining > 2.0:
        stages.append(2.0)
        remaining /= 2.0
    while remaining < 0.5:
        stages.append(0.5)
        remaining /= 0.5
    stages.append(remaining)
    return ",".join(f"atempo={value:.8f}" for value in stages)


def fit_segment_audio(
    source: str | Path,
    destination: str | Path,
    *,
    target_seconds: float,
    generated_seconds: float,
    ffmpeg_path: str,
    sample_rate: int = 24_000,
) -> float:
    factor = speed_factor(generated_seconds, target_seconds)
    output = Path(destination).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.part.wav")
    filters = (
        "silenceremove=start_periods=1:start_duration=0.02:start_threshold=-40dB,"
        f"{atempo_chain(factor)},apad=pad_dur={target_seconds:.6f},"
        f"atrim=0:{target_seconds:.6f}"
    )
    completed = subprocess.run(
        [
            ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(Path(source).expanduser().resolve()),
            "-af",
            filters,
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-c:a",
            "pcm_s16le",
            str(temporary),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
        shell=False,
    )
    if completed.returncode != 0:
        temporary.unlink(missing_ok=True)
        last_line = next(
            (line.strip() for line in reversed(completed.stderr.splitlines()) if line.strip()),
            "FFmpeg dublaj segmentini süreye oturtamadı.",
        )
        raise RuntimeError(last_line[:500])
    atomic_replace(temporary, output)
    return factor


def assemble_timeline(
    cues: list[Cue],
    segment_files: list[str | Path],
    destination: str | Path,
    *,
    sample_rate: int = 24_000,
) -> None:
    if len(cues) != len(segment_files) or not cues:
        raise ValueError("Dublaj zaman çizelgesi segmentleri eşleşmiyor.")
    import numpy as np
    import soundfile as sf

    output = Path(destination).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.part.wav")
    total_frames = math.ceil(timestamp_seconds(cues[-1].end) * sample_rate)
    silence = np.zeros(sample_rate, dtype=np.float32)
    with sf.SoundFile(
        temporary,
        mode="w",
        samplerate=sample_rate,
        channels=1,
        subtype="PCM_16",
        format="WAV",
    ) as timeline:
        remaining = total_frames
        while remaining:
            count = min(remaining, len(silence))
            timeline.write(silence[:count])
            remaining -= count
    with sf.SoundFile(temporary, mode="r+") as timeline:
        for cue, raw_segment in zip(cues, segment_files, strict=True):
            segment, rate = sf.read(
                str(Path(raw_segment).expanduser().resolve()),
                dtype="float32",
                always_2d=False,
            )
            if rate != sample_rate:
                raise RuntimeError("Dublaj segmenti örnekleme hızı eşleşmiyor.")
            if getattr(segment, "ndim", 1) > 1:
                segment = segment.mean(axis=1)
            start = round(timestamp_seconds(cue.start) * sample_rate)
            count = min(len(segment), total_frames - start)
            if count <= 0:
                continue
            timeline.seek(start)
            current = timeline.read(count, dtype="float32", always_2d=False)
            mixed = np.clip(current + segment[: len(current)], -1.0, 1.0)
            timeline.seek(start)
            timeline.write(mixed)
    atomic_replace(temporary, output)


def normalize_timeline(
    source: str | Path,
    destination: str | Path,
    *,
    ffmpeg_path: str,
    sample_rate: int = 48_000,
) -> None:
    output = Path(destination).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.part.wav")
    completed = subprocess.run(
        [
            ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(Path(source).expanduser().resolve()),
            "-af",
            f"loudnorm=I={TARGET_LUFS}:TP={TARGET_TRUE_PEAK_DBFS}:LRA=11",
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-c:a",
            "pcm_s16le",
            str(temporary),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=4 * 60 * 60,
        shell=False,
    )
    if completed.returncode != 0:
        temporary.unlink(missing_ok=True)
        raise RuntimeError("Dublaj ses seviyesi normalleştirilemedi.")
    atomic_replace(temporary, output)


def audio_loudness(path: str | Path, *, ffmpeg_path: str) -> dict[str, float | None]:
    completed = subprocess.run(
        [
            ffmpeg_path,
            "-hide_banner",
            "-nostats",
            "-i",
            str(Path(path).expanduser().resolve()),
            "-filter_complex",
            "ebur128=peak=true",
            "-f",
            "null",
            "-",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=4 * 60 * 60,
        shell=False,
    )
    text = completed.stderr
    loudness = re.findall(r"I:\s*(-?\d+(?:\.\d+)?)\s+LUFS", text)
    peaks = re.findall(r"Peak:\s*(-?\d+(?:\.\d+)?)\s+dBFS", text)
    return {
        "integrated_lufs": float(loudness[-1]) if loudness else None,
        "true_peak_dbfs": float(peaks[-1]) if peaks else None,
    }


def build_timing_quality(
    metrics: list[SegmentMetric],
    *,
    output_audio: str | Path,
    ffmpeg_path: str,
) -> dict[str, object]:
    if not metrics:
        raise ValueError("Dublaj kalite raporu için segment bulunamadı.")
    normal_count = sum(item.status == "normal" for item in metrics)
    rewrite = [item.index for item in metrics if item.status == "needs_rewrite"]
    leading_ms = sorted(item.leading_silence_seconds * 1000 for item in metrics)
    p95_index = max(0, math.ceil(len(leading_ms) * 0.95) - 1)
    loudness = audio_loudness(output_audio, ffmpeg_path=ffmpeg_path)
    normal_percent = round(normal_count * 100 / len(metrics), 2)
    median_onset = round(statistics.median(leading_ms), 2)
    p95_onset = round(leading_ms[p95_index], 2)
    errors: list[str] = []
    warnings: list[str] = []
    peak = loudness["true_peak_dbfs"]
    integrated = loudness["integrated_lufs"]
    if isinstance(peak, float) and peak > TARGET_TRUE_PEAK_DBFS + 0.1:
        errors.append("Dublaj tepe seviyesi -1 dBFS sınırını aşıyor.")
    if isinstance(integrated, float) and not -18 <= integrated <= -14:
        warnings.append("Dublaj bütünleşik ses seviyesi -16 LUFS ±2 dışında.")
    if median_onset > MAX_MEDIAN_ONSET_MS:
        errors.append("Ortanca konuşma başlangıcı 250 ms sınırını aşıyor.")
    if p95_onset > MAX_P95_ONSET_MS:
        errors.append("Konuşma başlangıçlarının %95 eşiği 500 ms sınırını aşıyor.")
    if normal_percent < 95:
        warnings.append("Segmentlerin %95'i normal 0,90–1,10 hız aralığında değil.")
    if rewrite:
        warnings.append("Süre için metin kısaltması gereken segmentler var.")
    return {
        "schema_version": 1,
        "passed": not errors,
        "semantic_review_required": True,
        "segment_count": len(metrics),
        "normal_speed_count": normal_count,
        "normal_speed_percent": normal_percent,
        "needs_rewrite_indices": rewrite,
        "median_leading_silence_ms": median_onset,
        "p95_leading_silence_ms": p95_onset,
        **loudness,
        "errors": errors,
        "warnings": warnings,
        "segments": [item.to_dict() for item in metrics],
    }


def normalized_characters(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(character for character in decomposed if character.isalnum())


def character_error_rate(reference: str, hypothesis: str) -> float:
    expected = normalized_characters(reference)
    actual = normalized_characters(hypothesis)
    if not expected:
        return 0.0 if not actual else 1.0
    previous = list(range(len(actual) + 1))
    for row, expected_character in enumerate(expected, start=1):
        current = [row]
        for column, actual_character in enumerate(actual, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1]
                    + (expected_character != actual_character),
                )
            )
        previous = current
    return previous[-1] / len(expected)


def build_asr_quality(
    target_cues: list[Cue], recognized_cues: list[Cue]
) -> dict[str, object]:
    reference = transcript_text(target_cues)
    hypothesis = transcript_text(recognized_cues)
    rate = character_error_rate(reference, hypothesis)
    return {
        "character_error_rate": round(rate, 4),
        "character_error_percent": round(rate * 100, 2),
        "asr_passed": rate <= 0.10,
        "target_character_count": len(normalized_characters(reference)),
        "recognized_character_count": len(normalized_characters(hypothesis)),
    }


def mux_dubbed_video(
    source_video: str | Path,
    dubbed_audio: str | Path,
    destination: str | Path,
    *,
    ffmpeg_path: str,
    ffprobe_path: str,
    original_volume: float = 0.12,
) -> dict[str, Any]:
    if not 0 <= original_volume <= 1:
        raise ValueError("Orijinal ses seviyesi 0–1 arasında olmalıdır.")
    source = Path(source_video).expanduser().resolve()
    audio = Path(dubbed_audio).expanduser().resolve()
    output = Path(destination).expanduser().resolve()
    if not source.is_file() or not audio.is_file():
        raise FileNotFoundError("Dublaj birleştirmesi için video veya ses bulunamadı.")
    if output.suffix.casefold() not in {".mp4", ".mkv"}:
        raise ValueError("Dublaj çıktısı MP4 veya MKV olmalıdır.")
    source_info = probe_local_media(str(source), ffprobe_path=ffprobe_path)
    temporary = output.with_name(f".{output.name}.part{output.suffix.lower()}")
    argv = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-i",
        str(audio),
    ]
    if int(source_info.get("audio_stream_count") or 0) > 0:
        argv.extend(
            (
                "-filter_complex",
                f"[0:a:0]volume={original_volume:.3f}[original];"
                "[original][1:a:0]amix=inputs=2:duration=first:dropout_transition=0:"
                "normalize=0,alimiter=limit=0.891:attack=5:release=50:latency=1[mixed]",
                "-map",
                "0:v:0",
                "-map",
                "[mixed]",
            )
        )
    else:
        argv.extend(("-map", "0:v:0", "-map", "1:a:0"))
    argv.extend(("-c:v", "copy", "-c:a", "aac", "-b:a", "192k"))
    if output.suffix.casefold() == ".mp4":
        argv.extend(("-movflags", "+faststart"))
    argv.extend(("-metadata:s:a:0", "language=tur", str(temporary)))
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
            "FFmpeg Türkçe dublaj videosunu oluşturamadı.",
        )
        raise RuntimeError(last_line[:500])
    atomic_replace(temporary, output)
    verification = verify_media_file(
        output, ffprobe_path=ffprobe_path, require_audio=True
    )
    verification["audio_quality"] = audio_loudness(output, ffmpeg_path=ffmpeg_path)
    return verification
