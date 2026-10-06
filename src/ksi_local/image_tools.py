"""Memory-bounded local image tools that always publish a new file."""

from __future__ import annotations

import hashlib
import math
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from PIL import Image, ImageFilter, ImageOps, UnidentifiedImageError

from ksi_local.atomic_files import atomic_replace


SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}
OUTPUT_FORMATS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}
MAX_IMAGE_PIXELS = 100_000_000
MAX_WORKING_BYTES = 1536 * 1024**2
PREVIEW_EDGE = 512


@dataclass(frozen=True)
class ImageInspection:
    path: str
    width: int
    height: int
    mode: str
    format: str
    frames: int
    source_bytes: int
    estimated_working_bytes: int
    metadata_keys: tuple[str, ...]
    fits_memory: bool


@dataclass(frozen=True)
class ResizePlan:
    source: ImageInspection
    width: int
    height: int
    output_format: str
    lossless: bool
    strip_metadata: bool
    super_resolution_pilot: bool
    estimated_output_bytes: int
    required_free_bytes: int
    free_bytes: int
    fits: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _source_path(path: str | Path) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise ValueError("Kaynak desteklenen, symlink olmayan normal bir görsel dosyası olmalıdır.")
    source = candidate.resolve()
    if not source.is_file() or source.suffix.casefold() not in SUPPORTED_IMAGE_SUFFIXES:
        raise ValueError("Kaynak desteklenen, symlink olmayan normal bir görsel dosyası olmalıdır.")
    return source


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_image(path: str | Path) -> ImageInspection:
    source = _source_path(path)
    try:
        with Image.open(source) as image:
            raw_width, raw_height = image.size
            pixels = raw_width * raw_height
            if raw_width <= 0 or raw_height <= 0 or pixels > MAX_IMAGE_PIXELS:
                raise ValueError("Görsel piksel sayısı güvenli sınırı aşıyor.")
            oriented = ImageOps.exif_transpose(image)
            width, height = oriented.size
            frames = int(getattr(image, "n_frames", 1))
            if frames != 1:
                raise ValueError("Bu sürüm yalnız tek kareli görselleri işler.")
            estimated = pixels * 4 * 6
            return ImageInspection(
                path=str(source),
                width=width,
                height=height,
                mode=image.mode,
                format=str(image.format or source.suffix.lstrip(".")).upper(),
                frames=frames,
                source_bytes=source.stat().st_size,
                estimated_working_bytes=estimated,
                metadata_keys=tuple(sorted(str(key) for key in image.info)),
                fits_memory=estimated <= MAX_WORKING_BYTES,
            )
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError) as error:
        raise ValueError("Görsel güvenle okunamadı.") from error


def plan_resize(
    path: str | Path,
    *,
    width: int | None = None,
    height: int | None = None,
    percent: float | None = None,
    lock_aspect: bool = True,
    output_format: str = "PNG",
    lossless: bool = True,
    strip_metadata: bool = True,
    super_resolution_pilot: bool = False,
    free_bytes: int | None = None,
) -> ResizePlan:
    source = inspect_image(path)
    if not source.fits_memory:
        raise ValueError("Görsel 16 GB cihaz için güvenli çalışma belleği sınırını aşıyor.")
    fmt = output_format.upper()
    if fmt not in OUTPUT_FORMATS:
        raise ValueError("Çıktı biçimi JPEG, PNG veya WEBP olmalıdır.")
    if lossless and fmt == "JPEG":
        raise ValueError("JPEG kayıplıdır; kayıpsız çıktı için PNG veya WEBP seçin.")
    if percent is not None:
        if width is not None or height is not None or not 1 <= percent <= 400:
            raise ValueError("Yüzde 1–400 olmalı ve piksel ölçüleriyle birlikte kullanılmamalıdır.")
        target_width = max(1, round(source.width * percent / 100))
        target_height = max(1, round(source.height * percent / 100))
    else:
        if width is None and height is None:
            raise ValueError("En az bir hedef ölçü veya yüzde verilmelidir.")
        if width is not None and width <= 0 or height is not None and height <= 0:
            raise ValueError("Hedef ölçüler pozitif olmalıdır.")
        if lock_aspect:
            if width is not None and height is not None:
                if not math.isclose(width / height, source.width / source.height, rel_tol=0.01):
                    raise ValueError("En-boy oranı kilitliyken hedef ölçüler kaynak oranıyla eşleşmelidir.")
                target_width, target_height = width, height
            elif width is not None:
                target_width, target_height = width, max(1, round(width * source.height / source.width))
            else:
                assert height is not None
                target_width, target_height = max(1, round(height * source.width / source.height)), height
        else:
            target_width = width or source.width
            target_height = height or source.height
    enlarging = target_width > source.width or target_height > source.height
    if enlarging and not super_resolution_pilot:
        raise ValueError("Büyütme için isteğe bağlı yerel süper çözünürlük pilotu açıkça seçilmelidir.")
    if super_resolution_pilot and (
        target_width > source.width * 2 or target_height > source.height * 2
    ):
        raise ValueError("Hafif yerel süper çözünürlük pilotu en fazla 2× büyütür.")
    output_pixels = target_width * target_height
    work = max(source.estimated_working_bytes, output_pixels * 4 * 6)
    if output_pixels > MAX_IMAGE_PIXELS or work > MAX_WORKING_BYTES:
        raise ValueError("Hedef görsel 16 GB cihaz için güvenli bellek sınırını aşıyor.")
    estimated_output = output_pixels * (4 if fmt == "PNG" else 3)
    available = free_bytes if free_bytes is not None else os.statvfs(Path(path).resolve().parent).f_bavail * os.statvfs(Path(path).resolve().parent).f_frsize
    required = estimated_output * 2 + 256 * 1024**2
    return ResizePlan(
        source=source,
        width=target_width,
        height=target_height,
        output_format=fmt,
        lossless=lossless,
        strip_metadata=strip_metadata,
        super_resolution_pilot=super_resolution_pilot,
        estimated_output_bytes=estimated_output,
        required_free_bytes=required,
        free_bytes=available,
        fits=available >= required,
    )


def _validate_destination(source: Path, destination: str | Path, fmt: str) -> Path:
    unresolved = Path(destination).expanduser()
    if unresolved.is_symlink():
        raise ValueError("Görsel çıktısı sembolik bağlantı olamaz.")
    target = unresolved.resolve()
    if target == source:
        raise ValueError("Kaynak görsel değiştirilemez; farklı bir çıktı yolu seçin.")
    if target.exists():
        raise FileExistsError("Mevcut çıktı dosyasının üzerine yazılmaz.")
    if target.suffix.casefold() != OUTPUT_FORMATS[fmt]:
        raise ValueError(f"{fmt} çıktısı {OUTPUT_FORMATS[fmt]} uzantısını kullanmalıdır.")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    return target


def _atomic_save(image: Image.Image, target: Path, fmt: str, *, lossless: bool) -> None:
    descriptor, name = tempfile.mkstemp(prefix=f".{target.stem}.", suffix=target.suffix, dir=target.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        options: dict[str, Any] = {"format": fmt}
        if fmt == "JPEG":
            options.update(quality=92, optimize=True, progressive=True)
        elif fmt == "WEBP":
            options.update(lossless=lossless, quality=100 if lossless else 92, method=4)
        elif fmt == "PNG":
            options.update(optimize=True, compress_level=6)
        image.save(temporary, **options)
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        atomic_replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def resize_image(plan: ResizePlan, destination: str | Path) -> Path:
    if not plan.fits:
        raise OSError("Çıktı ve güvenlik payı için disk alanı yetersiz.")
    source = Path(plan.source.path)
    target = _validate_destination(source, destination, plan.output_format)
    original_hash = _sha256(source)
    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGBA")
        resized = image.resize((plan.width, plan.height), Image.Resampling.LANCZOS)
        if plan.super_resolution_pilot:
            resized = resized.filter(ImageFilter.UnsharpMask(radius=1.2, percent=90, threshold=3))
        if plan.output_format == "JPEG":
            background = Image.new("RGB", resized.size, "white")
            background.paste(resized, mask=resized.getchannel("A"))
            resized = background
        _atomic_save(resized, target, plan.output_format, lossless=plan.lossless)
    if _sha256(source) != original_hash:
        target.unlink(missing_ok=True)
        raise RuntimeError("Kaynak görsel beklenmedik biçimde değişti; çıktı reddedildi.")
    return target


def create_quality_preview(path: str | Path, destination: str | Path) -> Path:
    source = _source_path(path)
    unresolved = Path(destination).expanduser()
    if unresolved.is_symlink():
        raise FileExistsError("Ön izleme yeni bir normal dosyaya yazılmalıdır.")
    target = unresolved.resolve()
    if target.exists() or target == source:
        raise FileExistsError("Ön izleme yeni bir dosyaya yazılmalıdır.")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGBA")
        image.thumbnail((PREVIEW_EDGE, PREVIEW_EDGE), Image.Resampling.LANCZOS)
        _atomic_save(image, target, "PNG", lossless=True)
    return target


def lossless_transform(
    path: str | Path,
    destination: str | Path,
    *,
    operation: str,
) -> Path:
    """Rotate/flip pixels without interpolation and strip source metadata."""
    operations = {
        "rotate-90": Image.Transpose.ROTATE_90,
        "rotate-180": Image.Transpose.ROTATE_180,
        "rotate-270": Image.Transpose.ROTATE_270,
        "flip-horizontal": Image.Transpose.FLIP_LEFT_RIGHT,
        "flip-vertical": Image.Transpose.FLIP_TOP_BOTTOM,
    }
    if operation not in operations:
        raise ValueError("Desteklenmeyen kayıpsız dönüşüm.")
    inspection = inspect_image(path)
    if not inspection.fits_memory:
        raise ValueError("Görsel 16 GB cihaz için güvenli çalışma belleği sınırını aşıyor.")
    source = Path(inspection.path)
    suffix = Path(destination).suffix.casefold()
    fmt = "PNG" if suffix == ".png" else "WEBP" if suffix == ".webp" else ""
    if not fmt:
        raise ValueError("Kayıpsız dönüşüm çıktısı PNG veya WEBP olmalıdır.")
    target = _validate_destination(source, destination, fmt)
    original_hash = _sha256(source)
    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGBA")
        transformed = image.transpose(operations[operation])
        _atomic_save(transformed, target, fmt, lossless=True)
    if _sha256(source) != original_hash:
        target.unlink(missing_ok=True)
        raise RuntimeError("Kaynak görsel beklenmedik biçimde değişti; çıktı reddedildi.")
    return target


def remove_uniform_background(
    path: str | Path,
    destination: str | Path,
    *,
    tolerance: int = 28,
    feather: int = 12,
    product_shadow: bool = False,
) -> Path:
    """Remove a near-uniform corner background locally without loading an AI model."""
    inspection = inspect_image(path)
    if not inspection.fits_memory or not 1 <= tolerance <= 120 or not 0 <= feather <= 64:
        raise ValueError("Arka plan kaldırma ayarları güvenli sınırların dışında.")
    source = Path(inspection.path)
    target = _validate_destination(source, destination, "PNG" if Path(destination).suffix.casefold() == ".png" else "WEBP")
    original_hash = _sha256(source)
    with Image.open(source) as opened:
        rgba = ImageOps.exif_transpose(opened).convert("RGBA")
    data = np.asarray(rgba, dtype=np.int32).copy()
    corners = np.array((data[0, 0, :3], data[0, -1, :3], data[-1, 0, :3], data[-1, -1, :3]))
    background = np.median(corners, axis=0)
    distance = np.sqrt(np.sum((data[:, :, :3] - background) ** 2, axis=2))
    upper = tolerance + max(1, feather)
    alpha = np.clip((distance - tolerance) * 255 / max(1, upper - tolerance), 0, 255).astype(np.uint8)
    data[:, :, 3] = np.minimum(data[:, :, 3], alpha)
    foreground = Image.fromarray(data.astype(np.uint8), "RGBA")
    if product_shadow:
        shadow_alpha = foreground.getchannel("A").filter(ImageFilter.GaussianBlur(12))
        shadow = Image.new("RGBA", foreground.size, (0, 0, 0, 0))
        shifted = Image.new("L", foreground.size, 0)
        shifted.paste(shadow_alpha.point(lambda value: value * 0.28), (8, 10))
        shadow.putalpha(shifted)
        foreground = Image.alpha_composite(shadow, foreground)
    fmt = "PNG" if target.suffix.casefold() == ".png" else "WEBP"
    _atomic_save(foreground, target, fmt, lossless=True)
    if _sha256(source) != original_hash:
        target.unlink(missing_ok=True)
        raise RuntimeError("Kaynak görsel beklenmedik biçimde değişti; çıktı reddedildi.")
    return target


def batch_resize(plans: Iterable[tuple[ResizePlan, str | Path]]) -> tuple[Path, ...]:
    """Process sequentially so a 16 GB machine never holds a batch in memory."""
    outputs: list[Path] = []
    for plan, destination in plans:
        outputs.append(resize_image(plan, destination))
    return tuple(outputs)
