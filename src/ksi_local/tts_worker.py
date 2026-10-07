"""Isolated Chatterbox worker with per-segment crash-safe checkpoints."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import signal
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ksi_local.atomic_files import atomic_replace, atomic_write_json
from ksi_local.bundle_runtime import bundle_root
from ksi_local.dubbing import (
    SegmentMetric,
    assemble_timeline,
    build_timing_quality,
    clean_spoken_text,
    cue_duration,
    fit_segment_audio,
    load_voice_profile,
    normalize_timeline,
    speed_factor,
    speed_status,
    text_sha256,
    timestamp_seconds,
)
from ksi_local.media import sha256_file
from ksi_local.privacy import redact_sensitive_text
from ksi_local.resource_governor import serialized_model
from ksi_local.subtitles import Cue, read_srt
from ksi_local.worker_protocol import WorkerEvent, encode_worker_event
from ksi_local.network_policy import local_only_socket_guard, local_worker_environment


CHECKPOINT_SCHEMA = 1
SPEECH_THRESHOLD = 0.01


def _prepare_numba_cache() -> Path:
    configured = os.environ.get("KSI_NUMBA_CACHE_DIRECTORY")
    target = (
        Path(configured).expanduser()
        if configured
        else Path.home() / "Library/Caches/KSI Local Studio/numba"
    )
    if target.is_symlink():
        raise ValueError("Numba cache hedefi sembolik bağlantı olamaz.")
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    if target.is_symlink() or not target.is_dir():
        raise ValueError("Numba cache hedefi geçerli bir klasör değildir.")
    target.chmod(0o700)
    resolved = target.resolve()
    os.environ["NUMBA_CACHE_DIR"] = str(resolved)
    return resolved


def _emit(event: WorkerEvent) -> None:
    print(encode_worker_event(event), flush=True)


def _handle_termination(signum: int, _frame: object) -> None:
    raise SystemExit(128 + signum)


def _audio_measurements(path: Path) -> tuple[float, float, float | None]:
    import numpy as np
    import soundfile as sf

    samples, sample_rate = sf.read(str(path), dtype="float32", always_2d=False)
    if getattr(samples, "ndim", 1) > 1:
        samples = samples.mean(axis=1)
    duration = len(samples) / sample_rate
    absolute = np.abs(samples)
    peak = float(absolute.max()) if len(absolute) else 0.0
    peak_dbfs = 20 * math.log10(peak) if peak > 0 else None
    audible = np.flatnonzero(absolute >= SPEECH_THRESHOLD)
    leading = float(audible[0] / sample_rate) if len(audible) else duration
    return duration, leading, peak_dbfs


def _checkpoint_header(
    *, input_sha256: str, profile_sha256: str, model_directory: Path
) -> dict[str, object]:
    return {
        "schema_version": CHECKPOINT_SCHEMA,
        "input_sha256": input_sha256,
        "profile_sha256": profile_sha256,
        "model_directory": str(model_directory),
        "segments": {},
    }


def _load_checkpoint(
    path: Path,
    *,
    input_sha256: str,
    profile_sha256: str,
    model_directory: Path,
) -> dict[str, Any]:
    expected = _checkpoint_header(
        input_sha256=input_sha256,
        profile_sha256=profile_sha256,
        model_directory=model_directory,
    )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return expected
    # The whole SRT hash is audit metadata only. Per-cue hashes below let one
    # corrected sentence regenerate without invalidating every accepted segment.
    keys = ("schema_version", "profile_sha256", "model_directory")
    if not isinstance(payload, dict) or any(payload.get(key) != expected[key] for key in keys):
        return expected
    payload["input_sha256"] = input_sha256
    if not isinstance(payload.get("segments"), dict):
        payload["segments"] = {}
    return payload


def _restored_segment(
    cue: Cue, *, segment_directory: Path, checkpoint: dict[str, Any]
) -> tuple[Path, SegmentMetric] | None:
    segments = checkpoint.get("segments")
    if not isinstance(segments, dict):
        return None
    entry = segments.get(str(cue.index))
    if not isinstance(entry, dict) or entry.get("text_sha256") != text_sha256(cue):
        return None
    filename = entry.get("fitted_filename")
    metric_payload = entry.get("metric")
    if not isinstance(filename, str) or Path(filename).name != filename:
        return None
    fitted = segment_directory / filename
    if (
        not fitted.is_file()
        or not isinstance(entry.get("audio_sha256"), str)
        or sha256_file(fitted) != entry["audio_sha256"]
        or not isinstance(metric_payload, dict)
    ):
        return None
    try:
        return fitted, SegmentMetric(**metric_payload)
    except (TypeError, ValueError):
        return None


def _write_generated(path: Path, waveform: Any, sample_rate: int) -> None:
    import soundfile as sf

    temporary = path.with_name(f".{path.name}.part.wav")
    samples = waveform.squeeze().detach().cpu().numpy()
    sf.write(str(temporary), samples, sample_rate, subtype="PCM_16", format="WAV")
    atomic_replace(temporary, path)


def synthesize(args: argparse.Namespace) -> dict[str, object]:
    source = Path(args.input_srt).expanduser().resolve()
    output = Path(args.output_wav).expanduser().resolve()
    report_path = Path(args.report).expanduser().resolve()
    segment_directory = Path(args.segments_directory).expanduser().resolve()
    raw_model_directory = Path(args.model_directory).expanduser()
    if raw_model_directory.is_symlink():
        raise ValueError("Ses modeli klasörü sembolik bağlantı olamaz.")
    model_directory = raw_model_directory.resolve()
    cpu = getattr(args, "engine", "chatterbox") == "piper"
    if cpu:
        from ksi_local.piper_backend import verified_voice
        profile = verified_voice(model_directory)
    else:
        profile = load_voice_profile(args.voice_profile, bundled_default=bundle_root() is not None)
    if not cpu and profile.engine != "chatterbox-multilingual-v3":
        raise ValueError("Kabul edilen Chatterbox Multilingual V3 profili gerekli.")
    if not source.is_file() or not model_directory.is_dir():
        raise FileNotFoundError("Dublaj altyazısı veya Chatterbox modeli bulunamadı.")
    checkpoint_identity = profile.profile_sha256
    resources = bundle_root()
    if not cpu and resources is not None:
        from ksi_local.speech_model_integrity import verify_chatterbox
        model_identity = verify_chatterbox(model_directory, resources)
        checkpoint_identity = hashlib.sha256(
            f"{profile.profile_sha256}:{model_identity}".encode("ascii")
        ).hexdigest()
    if args.max_segments is not None and args.max_segments < 1:
        raise ValueError("Pilot segment sayısı en az bir olmalıdır.")

    cues = read_srt(source)
    if args.max_segments is not None:
        cues = cues[: args.max_segments]
    if not cues:
        raise ValueError("Seslendirilecek Türkçe altyazı segmenti bulunamadı.")
    segment_directory.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path = segment_directory / "checkpoint.json"
    checkpoint = _load_checkpoint(
        checkpoint_path,
        input_sha256=sha256_file(source),
        profile_sha256=checkpoint_identity,
        model_directory=model_directory,
    )

    forced = set(args.regenerate_segment or ())
    known_indices = {cue.index for cue in cues}
    if forced - known_indices:
        raise ValueError("Yenilenecek dublaj segmenti altyazıda bulunamadı.")

    restored: dict[int, tuple[Path, SegmentMetric]] = {}
    missing: list[Cue] = []
    for cue in cues:
        reusable = (
            None
            if cue.index in forced
            else _restored_segment(
                cue, segment_directory=segment_directory, checkpoint=checkpoint
            )
        )
        if reusable is None:
            missing.append(cue)
        else:
            restored[cue.index] = reusable

    model = None
    torch = None
    cpu_segments = {}
    if missing and cpu:
        from ksi_local.piper_backend import generate_segments
        _emit(WorkerEvent("started", "tts", message="Piper Turkish · CPU"))
        cpu_segments = generate_segments(missing, segment_directory, model_directory)
    if missing and not cpu:
        _prepare_numba_cache()
        import torch as torch_module
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS
        from chatterbox.models.tokenizers import tokenizer as tokenizer_module
        from ksi_local.turkish_tts_adapter import turkish_tokenizer_scope

        torch = torch_module
        _emit(
            WorkerEvent(
                "started",
                "tts",
                message="Yerel Türkçe ses modeli yükleniyor…",
            )
        )
        with turkish_tokenizer_scope(tokenizer_module):
            model = ChatterboxMultilingualTTS.from_local(
                model_directory,
                device=profile.device,
                t3_model="v3",
            )

    completed = len(restored)
    _emit(WorkerEvent("progress", "tts", completed=completed, total=len(cues) + 1))
    for cue in cues:
        if cue.index in restored:
            continue
        cue_hash = text_sha256(cue)
        raw = segment_directory / f"segment-{cue.index:06d}.raw.wav"
        fitted = segment_directory / f"segment-{cue.index:06d}.wav"
        if cpu:
            raw = cpu_segments[cue.index]
        else:
            assert model is not None and torch is not None
            torch.manual_seed(profile.seed)
        # Chatterbox emits a token-by-token tqdm bar to stderr. The GUI already
        # shows stable per-segment progress, so discard that terminal-only noise.
        with (
            Path(os.devnull).open("w", encoding="utf-8") as sink,
            contextlib.redirect_stderr(sink),
        ):
            if not cpu:
                waveform = model.generate(
                    clean_spoken_text(cue.text),
                    language_id=profile.language,
                    audio_prompt_path=None,
                    exaggeration=profile.exaggeration,
                    cfg_weight=profile.cfg_weight,
                )
        if not cpu:
            _write_generated(raw, waveform, model.sr)
        generated_seconds, _raw_leading, peak_dbfs = _audio_measurements(raw)
        target_seconds = cue_duration(cue)
        factor = fit_segment_audio(
            raw,
            fitted,
            target_seconds=target_seconds,
            generated_seconds=generated_seconds,
            ffmpeg_path=args.ffmpeg,
        )
        _fitted_seconds, leading, _fitted_peak = _audio_measurements(fitted)
        fitted_hash = sha256_file(fitted)
        metric = SegmentMetric(
            index=cue.index,
            start_seconds=round(timestamp_seconds(cue.start), 3),
            end_seconds=round(timestamp_seconds(cue.end), 3),
            target_seconds=round(target_seconds, 3),
            generated_seconds=round(generated_seconds, 3),
            speed_factor=round(factor, 4),
            leading_silence_seconds=round(leading, 4),
            peak_dbfs=round(peak_dbfs, 3) if peak_dbfs is not None else None,
            text_sha256=cue_hash,
            audio_sha256=fitted_hash,
            status=speed_status(speed_factor(generated_seconds, target_seconds)),
        )
        restored[cue.index] = (fitted, metric)
        segments = checkpoint.setdefault("segments", {})
        assert isinstance(segments, dict)
        segments[str(cue.index)] = {
            "text_sha256": cue_hash,
            "fitted_filename": fitted.name,
            "audio_sha256": fitted_hash,
            "metric": asdict(metric),
        }
        atomic_write_json(checkpoint_path, checkpoint)
        completed += 1
        _emit(WorkerEvent("progress", "tts", completed=completed, total=len(cues) + 1))

    atomic_write_json(checkpoint_path, checkpoint)
    timeline = segment_directory / "timeline.wav"
    segment_files = [restored[cue.index][0] for cue in cues]
    metrics = [restored[cue.index][1] for cue in cues]
    assemble_timeline(cues, segment_files, timeline)
    normalize_timeline(timeline, output, ffmpeg_path=args.ffmpeg)
    quality = build_timing_quality(metrics, output_audio=output, ffmpeg_path=args.ffmpeg)
    quality.update(
        {
            "engine": profile.engine,
            "voice_description": profile.description,
            "voice_profile_sha256": profile.profile_sha256,
            "voice_profile_policy": "local-default" if cpu or bundle_root() is not None else "user-accepted",
            "input_sha256": sha256_file(source),
            "output_sha256": sha256_file(output),
            "restored_segment_count": len(cues) - len(missing),
        }
    )
    atomic_write_json(report_path, quality)
    _emit(WorkerEvent("progress", "tts", completed=len(cues) + 1, total=len(cues) + 1))
    _emit(
        WorkerEvent(
            "completed",
            "tts",
            payload={
                "output": str(output),
                "quality_report": str(report_path),
                "segment_count": len(cues),
                "normal_speed_percent": quality["normal_speed_percent"],
                "needs_rewrite_count": len(quality["needs_rewrite_indices"]),
                "timing_passed": quality["passed"],
                "restored_segment_count": quality["restored_segment_count"],
            },
        )
    )
    return quality


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ksi_local-tts-worker")
    parser.add_argument("input_srt")
    parser.add_argument("output_wav")
    parser.add_argument("--segments-directory", required=True)
    parser.add_argument("--model-directory", required=True)
    parser.add_argument("--voice-profile", required=True)
    parser.add_argument("--engine", choices=("chatterbox", "piper"), default="chatterbox")
    parser.add_argument("--ffmpeg", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--max-segments", type=int, help=argparse.SUPPRESS)
    parser.add_argument(
        "--regenerate-segment",
        type=int,
        action="append",
        help="Yalnız belirtilen altyazı segmentini yeniden üret",
    )
    return parser


@serialized_model
def main(argv: list[str] | None = None) -> int:
    signal.signal(signal.SIGTERM, _handle_termination)
    args = build_parser().parse_args(argv)
    try:
        environment = local_worker_environment()
        os.environ.clear()
        os.environ.update(environment)
        with local_only_socket_guard():
            synthesize(args)
        return 0
    except (OSError, RuntimeError, ValueError, ImportError, TypeError) as error:
        _emit(WorkerEvent("error", "tts", message=redact_sensitive_text(str(error))))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
