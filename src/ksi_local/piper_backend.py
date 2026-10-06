"""Independent CPU speech engine adapter; no Piper code imported into KSI."""

import json
import hashlib
import os
import tempfile
import wave
from pathlib import Path

from ksi_local.atomic_files import atomic_write_text
from ksi_local.bundle_runtime import OfflinePayload, bundle_root, digest_file, tool_path
from ksi_local.dubbing import VoiceProfile, clean_spoken_text
from ksi_local.engine_runner import run_engine


def verified_voice(directory: Path) -> VoiceProfile:
    resources = bundle_root()
    if resources is None:
        raise RuntimeError("CPU ses modeli doğrulanmış çevrimdışı paket gerektirir.")
    payload = OfflinePayload.load(resources)
    model = directory / "tr_TR-fettah-medium.onnx"
    config = directory / "tr_TR-fettah-medium.onnx.json"
    for identifier, installed in (("piper-tr-fettah", model), ("piper-tr-fettah-config", config)):
        expected = next((entry for entry in payload.files if entry.role == "model" and entry.identifier == identifier), None)
        if expected is None or installed.is_symlink() or not installed.is_file() or installed.stat().st_size != expected.size or digest_file(installed) != expected.sha256:
            raise RuntimeError("CPU ses modeli bütünlük denetiminden geçmedi.")
    if config.stat().st_size > 1024**2:
        raise ValueError("CPU ses yapılandırması boyut sınırını aşıyor.")
    data = json.loads(config.read_text(encoding="utf-8"))
    if data.get("language", {}).get("code") != "tr_TR" or data.get("audio", {}).get("sample_rate") != 22050:
        raise ValueError("CPU ses modeli Türkçe Fettah yapılandırmasıyla eşleşmiyor.")
    return VoiceProfile(engine="piper-fettah-cpu", description="Turkish Fettah · CPU · no voice cloning",
        seed=0, language="tr", exaggeration=0, cfg_weight=0, device="cpu",
        model_relative_directory="models/tts/piper", profile_sha256=hashlib.sha256((digest_file(config) + digest_file(model)).encode("ascii")).hexdigest())


def generate_segments(cues, directory: Path, models: Path) -> dict[int, Path]:
    """One bounded engine invocation loads one CPU voice for all missing cues."""
    verified_voice(models)
    if not cues:
        return {}
    if len(cues) > 10000:
        raise ValueError("CPU ses segment sayısı güvenli sınırı aşıyor.")
    results = {}
    with tempfile.TemporaryDirectory(prefix=".ksi-piper-", dir=directory) as temporary:
        stage = Path(temporary)
        source = stage / "input.txt"
        atomic_write_text(source, "\n".join(clean_spoken_text(cue.text) for cue in cues) + "\n")
        output = stage / "audio"
        run_engine([str(tool_path("piper-python")), "-I", "-m", "piper", "--model", str(models / "tr_TR-fettah-medium.onnx"),
            "--input-file", str(source), "--output-dir", str(output),
            "--output-dir-naming", "timestamp"], cwd=stage, timeout=10800)
        files = list(output.glob("*.wav"))
        if any(not candidate.stem.isascii() or not candidate.stem.isdecimal() for candidate in files):
            raise RuntimeError("CPU ses motoru segment adlandırması eşleşmiyor.")
        files.sort(key=lambda candidate: int(candidate.stem))
        if len(files) != len(cues):
            raise RuntimeError("CPU ses motoru segment sayısı eşleşmiyor.")
        for cue, candidate in zip(cues, files, strict=True):
            if candidate.is_symlink() or candidate.stat().st_size > 64 * 1024**2:
                raise RuntimeError("CPU ses segmenti boyut sınırını aşıyor.")
            with wave.open(str(candidate), "rb") as audio:
                if audio.getnchannels() != 1 or audio.getsampwidth() != 2 or audio.getframerate() != 22050 or not 0 < audio.getnframes() / 22050 <= 180:
                    raise RuntimeError("CPU ses segmenti PCM doğrulamasından geçmedi.")
            target = directory / f"segment-{cue.index:06d}.piper.raw.wav"
            if target.is_symlink():
                raise ValueError("CPU ses segment hedefi symlink olamaz.")
            os.replace(candidate, target)
            results[cue.index] = target
    return results
