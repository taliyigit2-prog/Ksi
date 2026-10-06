"""Small, independently gated creative pilots for 16 GiB Macs.

The baseline deliberately uses deterministic classical algorithms.  Model-backed
experiments remain catalogued but disabled until a local benchmark proves them safe.
"""

from __future__ import annotations

import io
import math
import random
import re
import struct
import wave
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from xml.etree import ElementTree

from PIL import Image, ImageDraw

from ksi_local.atomic_files import atomic_write_bytes, atomic_write_json, atomic_write_text


class LabFeature(StrEnum):
    IMAGE = "image"
    CHARACTER = "character"
    VIDEO = "video"
    MUSIC = "music"
    AUDIO = "audio"
    SVG = "svg"


@dataclass(frozen=True)
class ModelCandidate:
    identifier: str
    license: str
    parameters_billions: float | None
    apple_silicon_path: str
    memory_verified: bool
    runtime_verified: bool
    quality_verified: bool
    enabled: bool = False


MODEL_CANDIDATES = (
    ModelCandidate("segmind/SSD-1B", "Apache-2.0", 1.0, "Core ML conversion candidate", False, False, False),
    ModelCandidate("stabilityai/stable-diffusion-3.5-medium", "Stability AI Community", None, "Core ML unverified", False, False, False),
    ModelCandidate("black-forest-labs/FLUX.1-schnell", "Apache-2.0", 12.0, "No verified 16 GiB path", False, False, False),
)


class LabPolicy:
    """Explicit feature allow-list; an omitted pilot is always off."""

    def __init__(self, enabled: set[LabFeature | str] | None = None) -> None:
        self.enabled = frozenset(LabFeature(item) for item in (enabled or set()))

    def require(self, feature: LabFeature) -> None:
        if feature not in self.enabled:
            raise PermissionError(f"{feature.value} yaratıcı pilotu açıkça etkinleştirilmedi.")

    def status(self) -> dict[str, bool]:
        return {feature.value: feature in self.enabled for feature in LabFeature}


def candidate_report() -> list[dict[str, object]]:
    """Return evidence fields, never an invented aggregate score."""
    return [asdict(item) for item in MODEL_CANDIDATES]


def _new_output(path: str | Path) -> Path:
    unresolved = Path(path).expanduser()
    if unresolved.is_symlink():
        raise FileExistsError("Yaratıcı pilot mevcut bir dosyanın üzerine yazmaz.")
    target = unresolved.resolve()
    if target.exists():
        raise FileExistsError("Yaratıcı pilot mevcut bir dosyanın üzerine yazmaz.")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    return target


def generate_pattern(
    output: str | Path, *, seed: int, width: int = 512, height: int = 512, policy: LabPolicy
) -> Path:
    policy.require(LabFeature.IMAGE)
    if not (64 <= width <= 1024 and 64 <= height <= 1024 and width * height <= 1_048_576):
        raise ValueError("Pilot görseli 64–1024 piksel ve en fazla 1 MP olmalıdır.")
    randomizer = random.Random(seed)
    image = Image.new("RGB", (width, height), (18, 22, 35))
    draw = ImageDraw.Draw(image, "RGBA")
    for _ in range(48):
        x, y = randomizer.randrange(width), randomizer.randrange(height)
        radius = randomizer.randrange(8, max(9, min(width, height) // 4))
        color = tuple(randomizer.randrange(48, 240) for _ in range(3)) + (96,)
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)
    buffer = io.BytesIO()
    image.save(buffer, "PNG", optimize=True)
    target = _new_output(output)
    atomic_write_bytes(target, buffer.getvalue())
    return target


def create_character(
    directory: str | Path,
    *,
    name: str,
    seed: int,
    traits: list[str],
    policy: LabPolicy,
    real_person_reference: bool = False,
) -> tuple[Path, Path]:
    policy.require(LabFeature.CHARACTER)
    if real_person_reference:
        raise PermissionError("Tutarlı karakter pilotu gerçek kişi taklidini kabul etmez.")
    clean_name = " ".join(name.split())[:80]
    clean_traits = [" ".join(item.split())[:120] for item in traits if item.strip()][:12]
    if not clean_name or not clean_traits:
        raise ValueError("Kurgusal karakter adı ve en az bir özellik gereklidir.")
    unresolved = Path(directory).expanduser()
    if unresolved.is_symlink():
        raise FileExistsError("Karakter klasörü zaten var; önceki kimlik korunur.")
    root = unresolved.resolve()
    if root.exists():
        raise FileExistsError("Karakter klasörü zaten var; önceki kimlik korunur.")
    root.mkdir(parents=True, mode=0o700)
    randomizer = random.Random(seed)
    skin = f"#{randomizer.randrange(0x705040, 0xE0B090):06x}"
    accent = f"#{randomizer.randrange(0x204080, 0xE060C0):06x}"
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">'
        f'<rect width="256" height="256" rx="32" fill="{accent}"/>'
        f'<circle cx="128" cy="104" r="62" fill="{skin}"/>'
        '<circle cx="105" cy="98" r="7"/><circle cx="151" cy="98" r="7"/>'
        '<path d="M96 135 Q128 160 160 135" fill="none" stroke="#222" stroke-width="8"/>'
        '</svg>'
    )
    card = root / "character-card.json"
    portrait = root / "identity.svg"
    atomic_write_json(card, {"schema_version": 1, "fictional": True, "name": clean_name, "seed": seed, "traits": clean_traits, "identity_reference": portrait.name})
    atomic_write_text(portrait, svg)
    return card, portrait


def generate_instrumental(
    output: str | Path,
    *,
    seed: int,
    duration_seconds: float,
    policy: LabPolicy,
    sample_rate: int = 16_000,
) -> Path:
    policy.require(LabFeature.MUSIC)
    if not (0.25 <= duration_seconds <= 30):
        raise ValueError("Müzik pilotu 0,25–30 saniye arasında olmalıdır.")
    randomizer = random.Random(seed)
    notes = [220.0, 261.63, 293.66, 329.63, 392.0]
    frames = bytearray()
    count = int(duration_seconds * sample_rate)
    for index in range(count):
        frequency = notes[min(len(notes) - 1, index * len(notes) // max(count, 1))]
        value = math.sin(2 * math.pi * frequency * index / sample_rate)
        value += 0.2 * math.sin(2 * math.pi * (frequency * 2) * index / sample_rate)
        value *= 0.18 + randomizer.random() * 0.005
        frames.extend(struct.pack("<h", int(max(-1, min(1, value)) * 32767)))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(frames)
    target = _new_output(output)
    atomic_write_bytes(target, buffer.getvalue())
    return target


def build_video_plan(
    image: str | Path, output: str | Path, *, duration_seconds: float, ffmpeg: str | Path, policy: LabPolicy
) -> tuple[str, ...]:
    policy.require(LabFeature.VIDEO)
    source = Path(image).expanduser().resolve()
    target = Path(output).expanduser().resolve()
    executable = Path(ffmpeg).expanduser().resolve()
    if not source.is_file() or source.is_symlink() or not executable.is_file():
        raise ValueError("Video pilotu için normal görsel ve FFmpeg dosyaları gereklidir.")
    if target.exists() or target == source or not (0.25 <= duration_seconds <= 15):
        raise ValueError("Video çıktısı yeni olmalı ve süre 0,25–15 saniye olmalıdır.")
    return (str(executable), "-nostdin", "-loop", "1", "-i", str(source), "-t", f"{duration_seconds:.3f}", "-vf", "scale='min(1280,iw)':-2:flags=lanczos,format=yuv420p", "-an", "-movflags", "+faststart", str(target))


def build_audio_edit_plan(
    source: str | Path,
    output: str | Path,
    *,
    start: float,
    duration: float,
    gain_db: float,
    ffmpeg: str | Path,
    policy: LabPolicy,
) -> tuple[str, ...]:
    policy.require(LabFeature.AUDIO)
    origin = Path(source).expanduser().resolve()
    target = Path(output).expanduser().resolve()
    executable = Path(ffmpeg).expanduser().resolve()
    if not origin.is_file() or origin.is_symlink() or not executable.is_file():
        raise ValueError("Ses pilotu için normal ses ve FFmpeg dosyaları gereklidir.")
    if target.exists() or target == origin or start < 0 or not (0.1 <= duration <= 30):
        raise ValueError("Ses çıktısı yeni olmalı ve süre 0,1–30 saniye olmalıdır.")
    if not -24 <= gain_db <= 12:
        raise ValueError("Ses kazancı -24 ile +12 dB arasında olmalıdır.")
    return (str(executable), "-nostdin", "-ss", f"{start:.3f}", "-i", str(origin), "-t", f"{duration:.3f}", "-af", f"volume={gain_db:.2f}dB,afade=t=in:d=0.05,afade=t=out:st={max(0.0, duration - 0.05):.3f}:d=0.05", str(target))


_UNSAFE_XML = re.compile(r"<!DOCTYPE|<!ENTITY", re.IGNORECASE)
_EXTERNAL_VALUE = re.compile(r"^(?:https?:|file:|data:|//)", re.IGNORECASE)


def sanitize_svg(svg_text: str, output: str | Path, *, policy: LabPolicy) -> Path:
    policy.require(LabFeature.SVG)
    if len(svg_text.encode("utf-8")) > 1_000_000 or _UNSAFE_XML.search(svg_text):
        raise ValueError("SVG etkin tanım veya aşırı büyük içerik içeriyor.")
    try:
        root = ElementTree.fromstring(svg_text)
    except ElementTree.ParseError as error:
        raise ValueError("SVG ayrıştırılamadı.") from error
    if root.tag.rsplit("}", 1)[-1].casefold() != "svg":
        raise ValueError("Kök öğe SVG olmalıdır.")
    for element in root.iter():
        name = element.tag.rsplit("}", 1)[-1].casefold()
        if name in {"script", "foreignobject", "iframe", "object", "embed"}:
            raise ValueError("SVG etkin içerik içeriyor.")
        for attribute, value in element.attrib.items():
            plain = attribute.rsplit("}", 1)[-1].casefold()
            normalized = value.strip()
            if plain.startswith("on") or plain in {"href", "src"} and _EXTERNAL_VALUE.match(normalized):
                raise ValueError("SVG dış kaynak veya olay çalıştırıcısı içeriyor.")
            if "url(" in normalized.casefold() and "url(#" not in normalized.casefold():
                raise ValueError("SVG dış kaynak içeriyor.")
    ElementTree.register_namespace("", "http://www.w3.org/2000/svg")
    rendered = ElementTree.tostring(root, encoding="unicode", short_empty_elements=True)
    target = _new_output(output)
    atomic_write_text(target, rendered)
    return target


def generate_emoji_svg(output: str | Path, *, seed: int, mood: str, policy: LabPolicy) -> Path:
    policy.require(LabFeature.SVG)
    if mood not in {"happy", "calm", "surprised"}:
        raise ValueError("Emoji duygusu happy, calm veya surprised olmalıdır.")
    color = random.Random(seed).choice(("#ffd54f", "#ffca28", "#ffb74d"))
    mouth = {"happy": 'd="M38 58 Q50 72 62 58"', "calm": 'd="M40 62 L60 62"', "surprised": 'd="M50 56 a7 10 0 1 0 0 20 a7 10 0 1 0 0-20"'}[mood]
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><circle cx="50" cy="50" r="45" fill="{color}"/><circle cx="35" cy="42" r="5"/><circle cx="65" cy="42" r="5"/><path {mouth} fill="none" stroke="#222" stroke-width="5" stroke-linecap="round"/></svg>'
    return sanitize_svg(svg, output, policy=policy)
