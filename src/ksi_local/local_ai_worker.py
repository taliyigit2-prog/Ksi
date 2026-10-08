"""One-shot offline ONNX/Argos worker; no heavy imports in the desktop process."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
from pathlib import Path

from ksi_local.atomic_files import atomic_write_json
from ksi_local.bundle_runtime import digest_file
from ksi_local.network_policy import local_only_socket_guard
from ksi_local.privacy import redact_sensitive_text
from ksi_local.resource_governor import single_model_lock


def _verified_model(value: str, expected: str) -> Path:
    path = Path(value).expanduser()
    if path.is_symlink() or not path.is_file() or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("Yerel model yolu veya bütünlük değeri geçersiz.")
    if digest_file(path) != expected:
        raise RuntimeError("Yerel model SHA-256 denetiminden geçmedi.")
    return path.resolve()


def _background_session(request: dict):
    model = _verified_model(request["model"], request["model_sha256"])
    from ksi_local.worker_caches import prepare_model_worker_cache
    prepare_model_worker_cache()
    import onnxruntime as ort
    from rembg import remove
    from rembg.sessions.u2netp import U2netpSession
    # A local-only session override has no code path to download weights or
    # select rembg's commercial/default/cloud model.
    class BundledSession(U2netpSession):
        @classmethod
        def download_models(cls, *args, **kwargs):
            return str(model)

    options = ort.SessionOptions()
    options.inter_op_num_threads = 1
    options.intra_op_num_threads = 2
    session = BundledSession("u2netp", options, providers=["CPUExecutionProvider"])
    return session, remove


def remove_background(request: dict) -> dict:
    from PIL import Image, ImageOps
    from ksi_local.image_tools import inspect_image

    inspection = inspect_image(request["source"])
    if not inspection.fits_memory or inspection.width * inspection.height > 25_000_000:
        raise ValueError("AI arka plan işlemi güvenli görsel/bellek sınırını aşıyor.")
    session, remove = _background_session(request)
    output = Path(request["destination"]).resolve()
    if output.exists() or output.suffix.lower() != ".png":
        raise ValueError("AI arka plan çıktısı yeni bir PNG dosyası olmalıdır.")
    with Image.open(inspection.path) as raw:
        image = ImageOps.exif_transpose(raw).convert("RGBA")
        result = remove(image, session=session)
        if not isinstance(result, Image.Image) or result.size != image.size:
            raise RuntimeError("Arka plan motoru beklenen boyutta görsel üretmedi.")
        alpha = result.convert("RGBA").getchannel("A")
        low, high = alpha.getextrema()
        if high == 0 or low == 255:
            raise RuntimeError("Arka plan motoru ayrıştırılabilir alpha maskesi üretmedi.")
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as stream:
            result.save(stream, format="PNG")
    return {"output": str(output), "width": inspection.width, "height": inspection.height, "engine": "rembg-u2netp-cpu"}


def remove_background_frames(request: dict) -> dict:
    from PIL import Image

    source, destination = Path(request["source"]), Path(request["destination"])
    if source.is_symlink() or not source.is_dir() or destination.is_symlink() or destination.exists():
        raise ValueError("Video kare çalışma alanı geçersiz.")
    frames = sorted(source.glob("frame-*.png"))
    if not 1 <= len(frames) <= 600:
        raise ValueError("Kısa video en fazla 600 kare olabilir.")
    if any(path.is_symlink() or not re.fullmatch(r"frame-[0-9]{6}\.png", path.name) for path in frames):
        raise ValueError("Video kare listesi geçersiz.")
    if any(path.name != f"frame-{index:06d}.png" for index, path in enumerate(frames, start=1)):
        raise ValueError("Video kare sırası eksik veya tutarsız.")
    session, remove = _background_session(request)
    destination.mkdir(mode=0o700)
    masks = 0
    foreground = 0
    dimensions = None
    for index, frame in enumerate(frames):
        with Image.open(frame) as image:
            if image.width > 1280 or image.height > 720 or getattr(image, "n_frames", 1) != 1:
                raise ValueError("Video kare boyutu sınırı aşıyor.")
            if dimensions is not None and image.size != dimensions:
                raise ValueError("Video kare boyutları tutarsız.")
            dimensions = image.size
            result = remove(image.convert("RGBA"), session=session)
            if not isinstance(result, Image.Image) or result.size != dimensions:
                raise RuntimeError("Video arka plan motoru beklenen kareyi üretmedi.")
            result = result.convert("RGBA")
            minimum, maximum = result.getchannel("A").getextrema()
            masks += int(minimum < 255)
            foreground += int(maximum > 0)
            with (destination / frame.name).open("xb") as stream:
                result.save(stream, format="PNG")
        print(f"KSI_FRAME_PROGRESS={index + 1}/{len(frames)}", flush=True)
    if not masks or not foreground:
        raise RuntimeError("Videoda ayrıştırılabilir arka plan maskesi üretilemedi.")
    return {"frames": len(frames), "width": dimensions[0], "height": dimensions[1],
            "engine": "rembg-u2netp-cpu", "masked_frames": masks}


def translate_local(request: dict) -> dict:
    package_root = Path(request["packages"]).resolve()
    cache = Path(request["cache"]).resolve()
    if not package_root.is_dir():
        raise RuntimeError("Çevrimdışı Argos dil paketi klasörü bulunamadı.")
    os.environ.update({
        "ARGOS_PACKAGES_DIR": str(package_root), "ARGOS_DEVICE_TYPE": "cpu",
        "ARGOS_INTER_THREADS": "1", "ARGOS_INTRA_THREADS": "2",
        "ARGOS_DEBUG": "0", "ARGOS_DEV_MODE": "1",
        "XDG_DATA_HOME": str(cache / "data"), "XDG_CONFIG_HOME": str(cache / "config"),
        "XDG_CACHE_HOME": str(cache / "cache"),
    })
    from argostranslate.package import get_installed_packages
    import ctranslate2

    source, target = request["source_language"], request["target_language"]
    if not isinstance(source, str) or not isinstance(target, str):
        raise ValueError("Çeviri dil kodları geçersiz.")
    texts = request["texts"]
    if not isinstance(texts, list) or not 1 <= len(texts) <= 1000 or any(
        not isinstance(text, str) or len(text) > 32000 for text in texts
    ) or sum(map(len, texts)) > 1_000_000:
        raise ValueError("Çeviri metin listesi güvenli sınırların dışında.")
    if source == target:
        return {"texts": texts, "engine": "identity"}
    packages = [package for package in get_installed_packages()
                if package.from_code == source and package.to_code == target and package.type == "translate"]
    if not packages:
        raise RuntimeError("Bu dil çifti için doğrudan çevrimdışı Argos paketi kurulu değil.")
    selected = sorted(packages, key=lambda package: str(package.package_path))[0]
    if not selected.package_path.resolve().is_relative_to(package_root):
        raise ValueError("Dil paketi izin verilen model kökünün dışında.")

    class BoundedSentencizer:
        def split_sentences(self, text):
            # Subtitle-sized chunks need no external sentence-boundary model.
            # Preserve paragraphs; split longer passages at bounded whitespace.
            pieces = []
            for sentence in re.split(r"(?<=[.!?。！？])\s+", text):
                while len(sentence) > 500:
                    boundary = sentence.rfind(" ", 0, 500)
                    boundary = boundary if boundary > 100 else 500
                    pieces.append(sentence[:boundary])
                    sentence = sentence[boundary:].lstrip()
                if sentence:
                    pieces.append(sentence)
            return pieces or [text]

    # Use Argos' package/tokenizer API without importing its sentence-model
    # providers. This avoids implicit SBD downloads and unnecessary Torch on Intel.
    translator = ctranslate2.Translator(str(selected.package_path / "model"),
        device="cpu", compute_type="int8", inter_threads=1, intra_threads=2)
    sentencizer = BoundedSentencizer()
    results = []
    try:
        for text in texts:
            paragraphs = []
            for paragraph in text.split("\n"):
                if not paragraph.strip():
                    paragraphs.append(paragraph)
                    continue
                tokens = [selected.tokenizer.encode(sentence) for sentence in sentencizer.split_sentences(paragraph)]
                if any(len(value) > 2048 for value in tokens):
                    raise ValueError("Argos cümle belirteci sayısı güvenli sınırı aşıyor.")
                prefix = [[selected.target_prefix]] * len(tokens) if selected.target_prefix else None
                batch = translator.translate_batch(tokens, target_prefix=prefix,
                    replace_unknowns=True, max_batch_size=2048, batch_type="tokens",
                    beam_size=4, num_hypotheses=1, length_penalty=0.2,
                    max_input_length=0, max_decoding_length=2048)
                translated = []
                for result in batch:
                    if not result.hypotheses or len(result.hypotheses[0]) >= 2048:
                        raise RuntimeError("Argos cümle çıktısı kesilmiş veya eksik.")
                    value = selected.tokenizer.decode(result.hypotheses[0])
                    if selected.target_prefix and value.startswith(selected.target_prefix):
                        value = value[len(selected.target_prefix):]
                    translated.append(value.strip())
                paragraphs.append(" ".join(translated))
            results.append("\n".join(paragraphs))
    finally:
        translator.unload_model()
    if len(results) != len(texts) or any(text.strip() and not translated.strip() for text, translated in zip(texts, results)):
        raise RuntimeError("Çeviri motoru eksik veya boş çıktı üretti.")
    return {"texts": results, "engine": "argos-direct-cpu", "source_language": source, "target_language": target}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("request")
    parser.add_argument("result")
    arguments = parser.parse_args()
    request_path = Path(arguments.request)
    result_path = Path(arguments.result)
    if request_path.stat().st_size > 4 * 1024**2 or result_path.exists():
        raise ValueError("Yerel işçi girdi/çıktısı geçersiz.")
    request = json.loads(request_path.read_text(encoding="utf-8"))
    logging.getLogger().setLevel(logging.ERROR)
    try:
        with single_model_lock(), local_only_socket_guard():
            if request.get("operation") == "remove_background":
                result = remove_background(request)
            elif request.get("operation") == "remove_background_frames":
                result = remove_background_frames(request)
            elif request.get("operation") == "translate":
                result = translate_local(request)
            else:
                raise ValueError("Yerel AI işçi işlemi desteklenmiyor.")
        atomic_write_json(result_path, {"ok": True, **result})
        return 0
    except Exception as error:
        atomic_write_json(result_path, {"ok": False, "error": redact_sensitive_text(str(error))[:1000]})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
