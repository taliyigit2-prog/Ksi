"""Offline-first release manifest validation and non-mutating update planning."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ksi_local.job_store import SCHEMA_VERSION
from ksi_local.project_metadata import PRODUCT_NAME
from ksi_local.release_verification import (
    has_apple_notarization,
    has_developer_id_signature,
)


_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[.-]([0-9A-Za-z.-]+))?$")


def _version(value: str) -> tuple[int, int, int, int, tuple[tuple[int, object], ...]]:
    match = _VERSION.fullmatch(value)
    if match is None:
        raise ValueError("Sürüm semantik biçimde olmalıdır.")
    major, minor, patch = (int(match.group(index)) for index in (1, 2, 3))
    suffix = match.group(4) or ""
    if not suffix:
        return major, minor, patch, 1, ()
    identifiers: list[tuple[int, object]] = []
    for part in suffix.split("."):
        if not part:
            raise ValueError("Sürüm semantik biçimde olmalıdır.")
        for token in re.findall(r"\d+|[A-Za-z-]+", part):
            identifiers.append((0, int(token)) if token.isdigit() else (1, token.casefold()))
    return major, minor, patch, 0, tuple(identifiers)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class ReleaseManifest:
    version: str
    package_filename: str
    size_bytes: int
    sha256: str
    minimum_database_schema: int
    maximum_database_schema: int
    architecture: str
    offline_complete: bool
    models_included: bool
    notarized: bool
    signing: str

    @classmethod
    def load(cls, path: str | Path) -> "ReleaseManifest":
        unresolved = Path(path).expanduser()
        if unresolved.is_symlink():
            raise ValueError("Sürüm manifesti normal dosya olmalıdır.")
        source = unresolved.resolve()
        if not source.is_file():
            raise ValueError("Sürüm manifesti normal dosya olmalıdır.")
        try:
            payload: dict[str, Any] = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("Sürüm manifesti okunamadı.") from error
        if payload.get("schema_version") != 1 or payload.get("product") != PRODUCT_NAME:
            raise ValueError("Sürüm manifesti KSI Local Studio için geçerli değil.")
        result = cls(
            version=str(payload.get("version") or ""),
            package_filename=str(payload.get("package_filename") or ""),
            size_bytes=int(payload.get("size_bytes") or 0),
            sha256=str(payload.get("sha256") or ""),
            minimum_database_schema=int(payload.get("minimum_database_schema") or 0),
            maximum_database_schema=int(payload.get("maximum_database_schema") or 0),
            architecture=str(payload.get("architecture") or ""),
            offline_complete=payload.get("offline_complete") is True,
            models_included=payload.get("models_included") is True,
            notarized=payload.get("notarized") is True,
            signing=str(payload.get("signing") or ""),
        )
        _version(result.version)
        if not re.fullmatch(r"[a-f0-9]{64}", result.sha256):
            raise ValueError("Paket SHA-256 değeri geçersiz.")
        if Path(result.package_filename).name != result.package_filename:
            raise ValueError("Paket adı klasör veya yol içeremez.")
        if result.size_bytes < 1:
            raise ValueError("Paket boyutu geçersiz.")
        if result.architecture != "arm64":
            raise ValueError("Bu sürüm Apple Silicon ARM64 için değil.")
        if result.models_included:
            raise ValueError("Temel uygulama paketine model ağırlığı eklenemez.")
        return result


@dataclass(frozen=True)
class UpdatePlan:
    current_version: str
    target_version: str
    package: str
    direction: str
    database_schema: int
    rollback_backup_required: bool
    offline: bool
    notarized: bool
    ready: bool


def plan_offline_update(
    manifest_path: str | Path,
    package_path: str | Path,
    *,
    current_version: str,
    allow_downgrade: bool = False,
    require_notarization: bool = True,
    signature_verifier=has_developer_id_signature,
    notarization_verifier=has_apple_notarization,
) -> UpdatePlan:
    manifest = ReleaseManifest.load(manifest_path)
    unresolved_package = Path(package_path).expanduser()
    if unresolved_package.is_symlink():
        raise ValueError("Güncelleme paketi normal dosya olmalıdır.")
    package = unresolved_package.resolve()
    if not package.is_file():
        raise ValueError("Güncelleme paketi normal dosya olmalıdır.")
    if package.name != manifest.package_filename:
        raise ValueError("Güncelleme paketi adı manifest ile uyuşmuyor.")
    if package.stat().st_size != manifest.size_bytes or sha256_file(package) != manifest.sha256:
        raise ValueError("Güncelleme paketi bozuk veya eksik; mevcut kurulum korunacak.")
    if not manifest.minimum_database_schema <= SCHEMA_VERSION <= manifest.maximum_database_schema:
        raise ValueError("Güncelleme mevcut iş veritabanı şemasıyla uyumlu değil.")
    current = _version(current_version)
    target = _version(manifest.version)
    direction = "upgrade" if target > current else "same" if target == current else "downgrade"
    if direction == "downgrade" and not allow_downgrade:
        raise PermissionError("Eski sürüme dönüş ayrıca açık onay gerektirir.")
    if not manifest.offline_complete:
        raise ValueError("Paket çevrimdışı kurulum için eksiksiz değil.")
    verified_signature = False
    verified_notarization = False
    if require_notarization:
        if not manifest.notarized or manifest.signing != "developer-id":
            raise PermissionError("Public güncelleme Apple Developer ID ile imzalı ve noter onaylı değil.")
        verified_signature = bool(signature_verifier(package))
        verified_notarization = bool(notarization_verifier(package))
        if not verified_signature or not verified_notarization:
            raise PermissionError("Paketin Developer ID imzası veya Apple notarizasyonu bağımsız doğrulanamadı.")
    return UpdatePlan(
        current_version=current_version,
        target_version=manifest.version,
        package=str(package),
        direction=direction,
        database_schema=SCHEMA_VERSION,
        rollback_backup_required=True,
        offline=True,
        notarized=verified_notarization if require_notarization else manifest.notarized,
        ready=True,
    )
