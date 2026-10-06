"""Offline public-resource catalog and strictly separate private bookmarks."""

from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ksi_local.atomic_files import atomic_write_json


PUBLIC_CATALOG_SCHEMA_VERSION = 1
PRIVATE_CATALOG_SCHEMA_VERSION = 1
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PUBLIC_CATALOG = PROJECT_ROOT / "config" / "public-catalog.json"
_SENSITIVE_QUERY = re.compile(
    r"(?i)(token|auth|password|passwd|secret|cookie|session|signature|credential|api[_-]?key)"
)


@dataclass(frozen=True)
class CatalogEntry:
    entry_id: str
    title_tr: str
    title_en: str
    title_ru: str
    url: str
    content_type: str
    languages: tuple[str, ...]
    license: str
    license_url: str
    region: str
    verified_at: str
    access_conditions: str
    official_source: str
    official: bool
    redistribution_verified: bool

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["languages"] = list(self.languages)
        return payload


@dataclass(frozen=True)
class PrivateBookmark:
    bookmark_id: str
    title: str
    url: str
    created_at: str
    note: str = ""
    legal_status: str = "not_assessed"
    auto_download: bool = False


def _https_url(raw: object, *, field: str) -> str:
    if not isinstance(raw, str):
        raise ValueError(f"{field} geçerli bir HTTPS adresi olmalıdır.")
    parsed = urlsplit(raw.strip())
    if parsed.scheme.casefold() != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError(f"{field} geçerli bir HTTPS adresi olmalıdır.")
    if parsed.port not in (None, 443):
        raise ValueError(f"{field} varsayılan HTTPS portunu kullanmalıdır.")
    return urlunsplit(("https", parsed.netloc.casefold(), parsed.path or "/", parsed.query, ""))


def _entry(raw: object) -> CatalogEntry:
    if not isinstance(raw, dict):
        raise ValueError("Public katalog kaydı nesne olmalıdır.")
    required_text = (
        "id", "title_tr", "title_en", "title_ru", "content_type", "license",
        "region", "verified_at", "access_conditions", "official_source",
    )
    for key in required_text:
        if not isinstance(raw.get(key), str) or not str(raw[key]).strip():
            raise ValueError(f"Public katalog alanı eksik: {key}")
    try:
        date.fromisoformat(str(raw["verified_at"]))
    except ValueError as error:
        raise ValueError("Doğrulama tarihi ISO biçiminde olmalıdır.") from error
    languages = raw.get("languages")
    if not isinstance(languages, list) or not languages or not all(
        isinstance(item, str) and item.strip() for item in languages
    ):
        raise ValueError("Public katalog dil listesi boş olamaz.")
    if raw.get("official") is not True or raw.get("redistribution_verified") is not True:
        raise ValueError("Public katalog yalnız resmî ve dağıtım izni doğrulanmış kayıt içerir.")
    return CatalogEntry(
        entry_id=str(raw["id"]),
        title_tr=str(raw["title_tr"]),
        title_en=str(raw["title_en"]),
        title_ru=str(raw["title_ru"]),
        url=_https_url(raw.get("url"), field="Kayıt URL'si"),
        content_type=str(raw["content_type"]),
        languages=tuple(str(item) for item in languages),
        license=str(raw["license"]),
        license_url=_https_url(raw.get("license_url"), field="Lisans URL'si"),
        region=str(raw["region"]),
        verified_at=str(raw["verified_at"]),
        access_conditions=str(raw["access_conditions"]),
        official_source=str(raw["official_source"]),
        official=True,
        redistribution_verified=True,
    )


def load_public_catalog(path: str | Path = DEFAULT_PUBLIC_CATALOG) -> tuple[CatalogEntry, ...]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != PUBLIC_CATALOG_SCHEMA_VERSION:
        raise ValueError("Public katalog şeması desteklenmiyor.")
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list):
        raise ValueError("Public katalog kayıt listesi geçersiz.")
    entries = tuple(_entry(item) for item in raw_entries)
    ids = [item.entry_id for item in entries]
    if len(ids) != len(set(ids)):
        raise ValueError("Public katalog kimlikleri benzersiz olmalıdır.")
    return entries


def _search_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    return "".join(character for character in normalized if not unicodedata.combining(character))


def search_public_catalog(
    query: str, entries: Iterable[CatalogEntry] | None = None
) -> tuple[CatalogEntry, ...]:
    candidates = tuple(entries) if entries is not None else load_public_catalog()
    needle = _search_key(query.strip())
    if not needle:
        return candidates
    return tuple(
        entry
        for entry in candidates
        if needle
        in _search_key(
            " ".join(
                (
                    entry.title_tr, entry.title_en, entry.title_ru, entry.content_type,
                    *entry.languages, entry.license, entry.official_source,
                )
            )
        )
    )


def private_catalog_path(application_support: str | Path | None = None) -> Path:
    override = os.environ.get("KSI_PRIVATE_CATALOG_FILE")
    if override:
        return Path(override).expanduser()
    root = (
        Path(application_support).expanduser()
        if application_support is not None
        else Path.home() / "Library" / "Application Support" / "KSI Local Studio"
    )
    return root / "private-catalog.local.json"


def sanitize_bookmark_url(raw_url: str) -> str:
    url = _https_url(raw_url, field="Özel yer imi URL'si")
    parsed = urlsplit(url)
    query = urlencode(
        [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if not _SENSITIVE_QUERY.search(key)],
        doseq=True,
    )
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))


def load_private_bookmarks(path: str | Path | None = None) -> tuple[PrivateBookmark, ...]:
    target = Path(path) if path is not None else private_catalog_path()
    if not target.exists():
        return ()
    payload = json.loads(target.read_text(encoding="utf-8"))
    if payload.get("schema_version") != PRIVATE_CATALOG_SCHEMA_VERSION:
        raise ValueError("Private katalog şeması desteklenmiyor.")
    bookmarks: list[PrivateBookmark] = []
    for raw in payload.get("bookmarks", ()):
        if not isinstance(raw, dict):
            raise ValueError("Private katalog kaydı geçersiz.")
        bookmarks.append(
            PrivateBookmark(
                bookmark_id=str(raw["bookmark_id"]),
                title=str(raw["title"]),
                url=sanitize_bookmark_url(str(raw["url"])),
                created_at=str(raw["created_at"]),
                note=str(raw.get("note", ""))[:1000],
                legal_status="not_assessed",
                auto_download=False,
            )
        )
    return tuple(bookmarks)


def add_private_bookmark(
    title: str,
    url: str,
    *,
    note: str = "",
    path: str | Path | None = None,
    created_at: str | None = None,
) -> PrivateBookmark:
    clean_title = title.strip()
    if not clean_title:
        raise ValueError("Özel yer imi başlığı boş olamaz.")
    target = Path(path) if path is not None else private_catalog_path()
    existing = load_private_bookmarks(target)
    safe_url = sanitize_bookmark_url(url)
    timestamp = created_at or date.today().isoformat()
    date.fromisoformat(timestamp)
    bookmark_id = f"private-{len(existing) + 1:04d}"
    bookmark = PrivateBookmark(
        bookmark_id=bookmark_id,
        title=clean_title[:300],
        url=safe_url,
        created_at=timestamp,
        note=note.strip()[:1000],
    )
    atomic_write_json(
        target,
        {
            "schema_version": PRIVATE_CATALOG_SCHEMA_VERSION,
            "purpose": "personal_bookmarks_only",
            "bookmarks": [asdict(item) for item in (*existing, bookmark)],
        },
        mode=0o600,
    )
    return bookmark
