"""Constrained local image engine adapters; original files are never replaced."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from ksi_local.bundle_runtime import tool_path
from ksi_local.engine_runner import OperationCancelled, run_engine
from ksi_local.image_tools import inspect_image


@dataclass(frozen=True)
class ImageEngineResult:
    output: str
    source_bytes: int
    output_bytes: int
    lossless: bool
    warnings: tuple[str, ...] = ()


def pixel_digest(path: Path) -> tuple[tuple[int, int], str]:
    digest = hashlib.sha256()
    with Image.open(path) as image:
        if image.width * image.height > 25_000_000 or getattr(image, "n_frames", 1) != 1:
            raise ValueError("Piksel doğrulaması yalnız sınırlandırılmış tek kareli görselde yapılır.")
        for y in range(0, image.height, 128):
            digest.update(image.crop((0, y, image.width, min(y + 128, image.height))).convert("RGBA").tobytes())
        return image.size, digest.hexdigest()


def _paths(source: str | Path, destination: str | Path):
    raw_source = Path(source).expanduser()
    raw_target = Path(destination).expanduser()
    if raw_source.is_symlink() or not raw_source.is_file() or raw_target.is_symlink():
        raise ValueError("Görsel yolları symlink olmayan normal dosyalar olmalıdır.")
    if not raw_target.is_absolute():
        raise ValueError("Çıktı yolu mutlak olmalıdır.")
    original, output = raw_source.resolve(), raw_target.resolve()
    if output.exists() or output == original:
        raise FileExistsError("Kaynak veya mevcut görsel çıktısına yazılmaz.")
    output.parent.mkdir(parents=True, exist_ok=True)
    return original, output


def optimize_png(
    source: str | Path, destination: str | Path, *, strip_metadata: bool = False,
    cancel: threading.Event | None = None,
) -> ImageEngineResult:
    original, output = _paths(source, destination)
    if original.suffix.lower() != ".png" or output.suffix.lower() != ".png":
        raise ValueError("Kayıpsız PNG optimizasyonu PNG girdi ve çıktı gerektirir.")
    inspection = inspect_image(original)
    if not inspection.fits_memory:
        raise ValueError("Görsel güvenli bellek sınırını aşıyor.")
    expected = pixel_digest(original)
    with tempfile.TemporaryDirectory(prefix=".ksi-png-", dir=output.parent) as temporary:
        candidate = Path(temporary) / "optimized.png"
        shutil.copyfile(original, candidate)
        args = [str(tool_path("oxipng")), "-o", "2", "--threads", "2", "--timeout", "60"]
        if strip_metadata:
            args += ["--strip", "all"]
        args += [str(candidate)]
        run_engine(args, timeout=90, cancel=cancel)
        if pixel_digest(candidate) != expected:
            raise RuntimeError("PNG piksel/alpha eşitliği doğrulanamadı; çıktı yayımlanmadı.")
        warnings: list[str] = []
        if candidate.stat().st_size >= original.stat().st_size:
            warnings.append("Bu görselde dosya boyutu azalmadı.")
            if not strip_metadata:
                shutil.copyfile(original, candidate)
        if cancel is not None and cancel.is_set():
            raise OperationCancelled("PNG işlemi iptal edildi.")
        os.link(candidate, output)
        return ImageEngineResult(str(output), original.stat().st_size, output.stat().st_size, True, tuple(warnings))


MAGICK_POLICY = """<?xml version="1.0" encoding="UTF-8"?>
<policymap>
  <policy domain="resource" name="memory" value="256MiB"/>
  <policy domain="resource" name="map" value="512MiB"/>
  <policy domain="resource" name="disk" value="1GiB"/>
  <policy domain="resource" name="width" value="16000"/>
  <policy domain="resource" name="height" value="16000"/>
  <policy domain="resource" name="list-length" value="240"/>
  <policy domain="resource" name="thread" value="2"/>
  <policy domain="resource" name="time" value="120"/>
  <policy domain="delegate" rights="none" pattern="*"/>
  <policy domain="filter" rights="none" pattern="*"/>
  <policy domain="path" rights="none" pattern="@*"/>
  <policy domain="coder" rights="none" pattern="*"/>
  <policy domain="coder" rights="read|write" pattern="{PNG,JPEG,WEBP,HEIC,AVIF,GIF,TIFF}"/>
</policymap>
"""


def convert_advanced_image(
    source: str | Path, destination: str | Path, *, width: int | None = None,
    quality: int = 85, strip_metadata: bool = False,
    cancel: threading.Event | None = None,
) -> ImageEngineResult:
    original, output = _paths(source, destination)
    allowed = {".png", ".jpg", ".jpeg", ".webp", ".heic", ".avif", ".gif", ".tif", ".tiff"}
    if original.suffix.lower() not in allowed or output.suffix.lower() not in allowed:
        raise ValueError("İleri görsel motorunda bu dosya biçimine izin verilmez.")
    if not 1 <= quality <= 100 or (width is not None and not 1 <= width <= 6000):
        raise ValueError("Kalite veya hedef genişlik güvenli aralığın dışında.")
    if original.stat().st_size > 512 * 1024**2:
        raise ValueError("Görsel dosyası boyut sınırını aşıyor.")
    with tempfile.TemporaryDirectory(prefix=".ksi-image-", dir=output.parent) as temporary:
        stage = Path(temporary)
        (stage / "policy.xml").write_text(MAGICK_POLICY, encoding="utf-8")
        environment = dict(os.environ, MAGICK_CONFIGURE_PATH=str(stage), MAGICK_TEMPORARY_PATH=str(stage))
        candidate = stage / ("result" + output.suffix.lower())
        args = [str(tool_path("magick")), str(original), "-auto-orient"]
        if width:
            args += ["-resize", f"{width}x>"]
        if strip_metadata:
            args += ["-strip"]
        args += ["-quality", str(quality), str(candidate)]
        run_engine(args, cwd=stage, timeout=150, cancel=cancel, environment=environment)
        if not candidate.is_file() or not candidate.stat().st_size:
            raise RuntimeError("Görsel motoru geçerli dosya üretmedi.")
        if cancel is not None and cancel.is_set():
            raise OperationCancelled("Görsel işlemi iptal edildi.")
        os.link(candidate, output)
        return ImageEngineResult(str(output), original.stat().st_size, output.stat().st_size, False)
