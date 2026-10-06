"""Auditable local prompt-edit previews and explicit product templates."""

from __future__ import annotations

import hashlib
import json
import random
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageEnhance, ImageOps

from ksi_local.atomic_files import atomic_write_json
from ksi_local.image_tools import OUTPUT_FORMATS, PREVIEW_EDGE, _atomic_save, inspect_image


EDIT_SCHEMA_VERSION = 1
CLASSIC_MODEL = "classic-local-v1"
MAX_SAFE_MODEL_BYTES = 6 * 1024**3
_COLOR = re.compile(r"#[0-9a-fA-F]{6}\b")


@dataclass(frozen=True)
class PlatformProfile:
    key: str
    name: str
    width: int
    height: int
    safe_margin_percent: int
    output_format: str
    max_file_bytes: int | None
    verified_at: str
    official_source: str


PLATFORM_PROFILES = {
    "etsy-listing-square": PlatformProfile(
        "etsy-listing-square",
        "Etsy listing square",
        2000,
        2000,
        12,
        "JPEG",
        None,
        "2026-10-01",
        "https://help.etsy.com/hc/en-us/articles/115015663347-Requirements-and-Best-Practices-for-Images-in-Your-Etsy-Shop",
    ),
    "shopify-product-square": PlatformProfile(
        "shopify-product-square",
        "Shopify product square",
        2048,
        2048,
        10,
        "PNG",
        20 * 1024**2,
        "2026-10-01",
        "https://help.shopify.com/en/manual/products/product-media/product-media-types",
    ),
}


@dataclass(frozen=True)
class EditRevision:
    revision: int
    source: str
    output: str
    prompt: str
    seed: int
    model: str
    workflow: str
    platform: str | None
    preview: bool
    sha256: str


def platform_profile(key: str | None) -> PlatformProfile:
    if not key:
        raise ValueError("Hedef platform açıkça seçilmelidir; varsayılan platform yoktur.")
    try:
        return PLATFORM_PROFILES[key]
    except KeyError as error:
        raise ValueError("Bilinmeyen hedef platform profili.") from error


def validate_model_budget(model: str, model_bytes: int = 0) -> None:
    if model != CLASSIC_MODEL:
        raise ValueError("Bu sürümde yalnız modelsiz, yerel klasik düzenleme pilotu etkindir.")
    if model_bytes < 0 or model_bytes > MAX_SAFE_MODEL_BYTES:
        raise ValueError("Model 16 GB birleşik bellek güvenlik bütçesini aşıyor.")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fit_product(image: Image.Image, profile: PlatformProfile) -> tuple[Image.Image, tuple[int, int, int, int]]:
    canvas = Image.new("RGBA", (profile.width, profile.height), "white")
    margin_x = round(profile.width * profile.safe_margin_percent / 100)
    margin_y = round(profile.height * profile.safe_margin_percent / 100)
    box_width = profile.width - margin_x * 2
    box_height = profile.height - margin_y * 2
    fitted = image.copy()
    fitted.thumbnail((box_width, box_height), Image.Resampling.LANCZOS)
    left = (profile.width - fitted.width) // 2
    top = (profile.height - fitted.height) // 2
    box = (left, top, left + fitted.width, top + fitted.height)
    canvas.alpha_composite(fitted, (left, top))
    return canvas, box


def _normalized_source(
    source: Path, workflow: str, profile: PlatformProfile | None
) -> tuple[Image.Image, tuple[int, int, int, int] | None]:
    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGBA")
    if workflow == "general":
        return image, None
    if workflow != "product":
        raise ValueError("İş akışı general veya product olmalıdır.")
    assert profile is not None
    return _fit_product(image, profile)


def _mask_for_canvas(
    mask_path: str | Path | None,
    source_size: tuple[int, int],
    canvas_size: tuple[int, int],
    product_box: tuple[int, int, int, int] | None,
) -> Image.Image | None:
    if mask_path is None:
        return None
    mask_source = Path(mask_path).expanduser().resolve()
    with Image.open(mask_source) as opened:
        mask = ImageOps.exif_transpose(opened).convert("L")
    if mask.size != source_size:
        raise ValueError("Koruma maskesi kaynak görselle aynı ölçüde olmalıdır.")
    if product_box is None:
        return mask
    left, top, right, bottom = product_box
    placed = Image.new("L", canvas_size, 0)
    placed.paste(mask.resize((right - left, bottom - top), Image.Resampling.NEAREST), (left, top))
    return placed


def _apply_prompt(image: Image.Image, prompt: str, protect_mask: Image.Image | None) -> Image.Image:
    command = " ".join(prompt.casefold().split())
    if not command or len(command) > 500:
        raise ValueError("Düzenleme istemi 1–500 karakter olmalıdır.")
    original = image.copy()
    result = image
    matched = False
    if any(word in command for word in ("aydınlat", "brighter", "parlak")):
        result = ImageEnhance.Brightness(result).enhance(1.18)
        matched = True
    if any(word in command for word in ("koyulaştır", "darker", "karart")):
        result = ImageEnhance.Brightness(result).enhance(0.82)
        matched = True
    if any(word in command for word in ("kontrast", "contrast")):
        result = ImageEnhance.Contrast(result).enhance(1.15)
        matched = True
    if any(word in command for word in ("siyah beyaz", "grayscale", "monochrome")):
        result = ImageOps.grayscale(result).convert("RGBA")
        matched = True
    color_match = _COLOR.search(prompt)
    if color_match:
        if protect_mask is None:
            raise ValueError("Arka plan rengini değiştirmek için ürün/logo koruma maskesi gerekir.")
        background = Image.new("RGBA", result.size, color_match.group(0))
        result = Image.composite(original, background, protect_mask)
        matched = True
    if not matched:
        raise ValueError("İstem bu güvenli pilotta desteklenen bir düzenleme içermiyor.")
    if protect_mask is not None and not color_match:
        result = Image.composite(original, result, protect_mask)
    return result


class EditSession:
    """Non-destructive revision history: undo changes the pointer, never deletes files."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory).expanduser().resolve()
        self.manifest = self.directory / "edit-session.json"

    @classmethod
    def create(
        cls,
        source: str | Path,
        directory: str | Path,
        *,
        workflow: str,
        platform: str | None = None,
    ) -> EditSession:
        inspection = inspect_image(source)
        if not inspection.fits_memory:
            raise ValueError("Görsel 16 GB cihaz güvenlik sınırını aşıyor.")
        if workflow == "product":
            platform_profile(platform)
        elif workflow != "general" or platform is not None:
            raise ValueError("Genel düzenleme platform seçmez; ürün düzenleme platform gerektirir.")
        session = cls(directory)
        if session.manifest.exists():
            raise FileExistsError("Düzenleme oturumu zaten var.")
        session.directory.mkdir(parents=True, exist_ok=False, mode=0o700)
        atomic_write_json(
            session.manifest,
            {
                "schema_version": EDIT_SCHEMA_VERSION,
                "original": inspection.path,
                "workflow": workflow,
                "platform": platform,
                "current_revision": 0,
                "revisions": [],
                "pending_preview": None,
            },
        )
        return session

    def _load(self) -> dict[str, Any]:
        payload = json.loads(self.manifest.read_text(encoding="utf-8"))
        if payload.get("schema_version") != EDIT_SCHEMA_VERSION:
            raise ValueError("Düzenleme oturumu şeması desteklenmiyor.")
        return payload

    def preview(
        self,
        prompt: str,
        *,
        protect_mask: str | Path | None = None,
        seed: int | None = None,
        model: str = CLASSIC_MODEL,
        model_bytes: int = 0,
    ) -> Path:
        validate_model_budget(model, model_bytes)
        payload = self._load()
        revisions = payload["revisions"]
        current = int(payload["current_revision"])
        source = Path(revisions[current - 1]["output"] if current else payload["original"])
        workflow = str(payload["workflow"])
        profile = platform_profile(payload["platform"]) if workflow == "product" else None
        with Image.open(source) as opened:
            source_size = opened.size
        image, box = _normalized_source(
            source, "general" if current else workflow, profile if not current else None
        )
        mask = _mask_for_canvas(protect_mask, source_size, image.size, box)
        image.thumbnail((PREVIEW_EDGE, PREVIEW_EDGE), Image.Resampling.LANCZOS)
        if mask is not None:
            mask = mask.resize(image.size, Image.Resampling.NEAREST)
        edited = _apply_prompt(image, prompt, mask)
        preview_path = self.directory / "preview.png"
        if preview_path.exists():
            preview_path.unlink()
        _atomic_save(edited, preview_path, "PNG", lossless=True)
        actual_seed = seed if seed is not None else random.SystemRandom().randrange(0, 2**31)
        payload["pending_preview"] = {
            "prompt": prompt,
            "seed": actual_seed,
            "model": model,
            "mask": str(Path(protect_mask).expanduser().resolve()) if protect_mask else None,
            "preview": str(preview_path),
            "preview_sha256": _digest(preview_path),
        }
        atomic_write_json(self.manifest, payload)
        return preview_path

    def commit(self, *, approve_preview: bool) -> Path:
        if not approve_preview:
            raise PermissionError("Tam çözünürlüklü çıktı için küçük ön izleme açıkça onaylanmalıdır.")
        payload = self._load()
        pending = payload.get("pending_preview")
        if not isinstance(pending, dict):
            raise ValueError("Onaylanacak bir ön izleme yok.")
        preview_path = Path(pending["preview"])
        if not preview_path.is_file() or _digest(preview_path) != pending["preview_sha256"]:
            raise ValueError("Ön izleme onaydan önce değişti.")
        current = int(payload["current_revision"])
        revisions = list(payload["revisions"][:current])
        source = Path(revisions[-1]["output"] if revisions else payload["original"])
        workflow = str(payload["workflow"])
        profile = platform_profile(payload["platform"]) if workflow == "product" else None
        with Image.open(source) as opened:
            source_size = opened.size
        image, box = _normalized_source(
            source, "general" if current else workflow, profile if not current else None
        )
        mask = _mask_for_canvas(pending.get("mask"), source_size, image.size, box)
        edited = _apply_prompt(image, str(pending["prompt"]), mask)
        revision_number = max(
            (int(item.get("revision", 0)) for item in payload["revisions"]), default=0
        ) + 1
        output_format = profile.output_format if profile else "PNG"
        output = self.directory / f"revision-{revision_number:03d}{OUTPUT_FORMATS[output_format]}"
        if output.exists():
            raise FileExistsError("Revizyon çıktısı zaten var; üzerine yazılmadı.")
        if output_format == "JPEG":
            flattened = Image.new("RGB", edited.size, "white")
            flattened.paste(edited, mask=edited.getchannel("A"))
            edited = flattened
        _atomic_save(edited, output, output_format, lossless=output_format != "JPEG")
        if profile and profile.max_file_bytes and output.stat().st_size > profile.max_file_bytes:
            output.unlink(missing_ok=True)
            raise ValueError("Çıktı seçili platformun dosya boyutu sınırını aşıyor.")
        revision = EditRevision(
            revision_number,
            str(source),
            str(output),
            str(pending["prompt"]),
            int(pending["seed"]),
            str(pending["model"]),
            workflow,
            payload.get("platform"),
            False,
            _digest(output),
        )
        revisions.append(asdict(revision))
        payload.update(current_revision=len(revisions), revisions=revisions, pending_preview=None)
        atomic_write_json(self.manifest, payload)
        return output

    def undo(self) -> Path:
        payload = self._load()
        current = int(payload["current_revision"])
        if current <= 0:
            raise ValueError("Geri alınacak düzenleme yok.")
        payload["current_revision"] = current - 1
        payload["pending_preview"] = None
        atomic_write_json(self.manifest, payload)
        if current - 1 == 0:
            return Path(payload["original"])
        return Path(payload["revisions"][current - 2]["output"])

    def comparison(self, destination: str | Path) -> Path:
        payload = self._load()
        current = int(payload["current_revision"])
        if current <= 0:
            raise ValueError("Karşılaştırılacak tamamlanmış revizyon yok.")
        target = Path(destination).expanduser().resolve()
        if target.exists() or target.suffix.casefold() != ".png":
            raise FileExistsError("Karşılaştırma yeni bir PNG dosyasına yazılmalıdır.")
        with Image.open(payload["original"]) as before_open, Image.open(
            payload["revisions"][current - 1]["output"]
        ) as after_open:
            before = ImageOps.exif_transpose(before_open).convert("RGBA")
            after = ImageOps.exif_transpose(after_open).convert("RGBA")
            before.thumbnail((PREVIEW_EDGE, PREVIEW_EDGE), Image.Resampling.LANCZOS)
            after.thumbnail((PREVIEW_EDGE, PREVIEW_EDGE), Image.Resampling.LANCZOS)
        panel = Image.new("RGBA", (before.width + after.width, max(before.height, after.height)), "white")
        panel.alpha_composite(before, (0, 0))
        panel.alpha_composite(after, (before.width, 0))
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        _atomic_save(panel, target, "PNG", lossless=True)
        return target
