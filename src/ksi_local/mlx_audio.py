"""Bounded local PCM decoding for MLX without its ambient PATH dependency."""

import math
import tempfile
from pathlib import Path

from ksi_local.bundle_runtime import tool_path
from ksi_local.engine_runner import run_engine
from ksi_local.internal_storage import validate_internal_path
from ksi_local.media import LOCAL_FORMAT_WHITELIST, MAX_DURATION_SECONDS


def decode_packaged_audio(source: Path, duration_seconds: float | None):
    if duration_seconds is None or not math.isfinite(duration_seconds) or not 0 < duration_seconds <= MAX_DURATION_SECONDS:
        raise ValueError("Paketli ses çözümlemesi sıfırdan büyük ve en fazla üç saatlik doğrulanmış süre gerektirir.")
    executable = str(tool_path("ffmpeg"))
    temporary_root = validate_internal_path(Path(tempfile.gettempdir()))
    # Bound the output independently of untrusted media duration metadata.
    maximum_bytes = (math.ceil(duration_seconds * 16000) + 16000) * 4
    with tempfile.TemporaryDirectory(prefix="ksi-local-pcm-", dir=temporary_root) as temporary:
        output = Path(temporary) / "audio.f32"
        run_engine([executable, "-hide_banner", "-nostdin", "-loglevel", "error",
            "-protocol_whitelist", "file,pipe", "-format_whitelist", LOCAL_FORMAT_WHITELIST,
            "-i", str(source), "-map", "0:a:0", "-vn", "-sn", "-dn",
            "-t", str(duration_seconds), "-ac", "1", "-ar", "16000", "-c:a", "pcm_f32le",
            "-f", "f32le", "-fs", str(maximum_bytes), str(output)], timeout=300)
        if output.is_symlink() or not output.is_file() or not 0 < output.stat().st_size <= maximum_bytes or output.stat().st_size % 4:
            raise RuntimeError("Paketli ses çözümleme çıktısı eksik veya güvenli boyut sınırının dışında.")
        import numpy
        audio = numpy.fromfile(output, dtype="<f4")
        if not numpy.isfinite(audio).all():
            raise RuntimeError("Paketli ses çözümlemesi geçersiz örnekler üretti.")
    return audio
