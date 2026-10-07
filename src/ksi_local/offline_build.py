"""Seal a clean staging payload against explicit pinned inputs and licenses.

This never derives approved hashes from a personal runtime, downloads models,
or labels an untested package as release-ready. Distribution tests are separate.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

from ksi_local.atomic_files import atomic_write_bytes
from ksi_local.bundle_runtime import ARCHITECTURES, MAX_MANIFEST_BYTES, MAX_PAYLOAD_FILES, OfflinePayload, PayloadFile, digest_file, safe_member


def seal_offline_payload(resources: Path, specification: dict) -> dict:
    if resources.is_symlink() or not resources.is_dir():
        raise ValueError("Temiz paket sahnesi normal bir klasör olmalıdır.")
    if not isinstance(specification, dict) or specification.get("schema_version") != 1:
        raise ValueError("Çevrimdışı yapı tanımı geçersiz.")
    architecture = specification.get("architecture")
    if architecture not in ARCHITECTURES:
        raise ValueError("Paket mimarisi açıkça arm64 veya x86_64 olmalıdır.")
    rows = specification.get("files")
    if not isinstance(rows, list) or not 1 <= len(rows) < MAX_PAYLOAD_FILES:
        raise ValueError("Sabitlenmiş paket dosya sayısı geçersiz.")
    entries, records, seen = [], {}, set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Sabitlenmiş paket dosyası nesne olmalıdır.")
        try:
            entry = PayloadFile(**{key: row[key] for key in PayloadFile.__annotations__})
        except (KeyError, TypeError) as error:
            raise ValueError("Sabitlenmiş paket dosyası eksik.") from error
        path = safe_member(resources, entry.path)
        key = (entry.role, entry.identifier)
        if key in records or entry.path.casefold() in seen:
            raise ValueError("Paket bileşeni veya dosya yolu çakışıyor.")
        if not isinstance(entry.sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", entry.sha256) or type(entry.size) is not int or entry.size < 0:
            raise ValueError("Onaylı dosya bütünlük değeri geçersiz.")
        if entry.role not in {"tool", "model", "license", "support"}:
            raise ValueError("Paket dosya rolü geçersiz.")
        if entry.role == "license" and entry.size == 0:
            raise ValueError("Paket lisans metni boş olamaz.")
        if not isinstance(entry.identifier, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", entry.identifier):
            raise ValueError("Paket bileşeni kimliği geçersiz.")
        if not path.is_file() or path.stat().st_size != entry.size or digest_file(path) != entry.sha256:
            raise RuntimeError(f"Sabitlenmiş temiz girdi doğrulanamadı: {entry.identifier}")
        if entry.role == "model" and not entry.path.startswith("models/"):
            raise ValueError("Model girdisi models klasöründe olmalıdır.")
        records[key] = row
        seen.add(entry.path.casefold())
        entries.append(entry)
    for entry in entries:
        row = records[(entry.role, entry.identifier)]
        # Native shared libraries are support files, not top-level tools.
        # Explicitly licensed support members must retain the same notices and
        # corresponding-source closure; changing role cannot bypass that gate.
        if entry.role not in {"tool", "model"} and not (entry.role == "support" and "license" in row):
            continue
        if not isinstance(row.get("license"), str) or ("license", row.get("license_file")) not in records:
            raise ValueError(f"Bileşen lisans metni eksik: {entry.identifier}")
        source = row.get("source_url")
        if not isinstance(source, str):
            raise ValueError("Bileşen resmî kaynak URL'si eksik.")
        parsed = urlsplit(source)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Bileşen kaynak URL'si herkese açık ve temiz HTTPS olmalıdır.")
        if not isinstance(row.get("revision"), str) or not row["revision"].strip():
            raise ValueError("Bileşen sabit kaynak sürümü eksik.")
        if re.search(r"\b(?:A?GPL|LGPL)(?:-|\b)", row["license"]):
            if ("support", row.get("corresponding_source")) not in records:
                raise ValueError("Copyleft motorun karşılık gelen kaynak arşivi eksik.")
    models = specification.get("models")
    if not isinstance(models, list) or not models:
        raise ValueError("Çevrimdışı paket model kataloğu boş olamaz.")
    known = {entry.identifier for entry in entries if entry.role == "model"}
    covered, model_ids = set(), set()
    for model in models:
        if not isinstance(model, dict) or not all(isinstance(model.get(key), str) and 0 < len(model[key]) <= 500 for key in ("id", "title", "description", "license")):
            raise ValueError("Model katalog tanımı geçersiz.")
        members = model.get("members")
        if model["id"] in model_ids or not isinstance(members, list) or not members or any(not isinstance(member, str) for member in members) or len(set(members)) != len(members) or not set(members) <= known:
            raise ValueError("Model katalog üyeleri eksik veya çakışıyor.")
        if covered.intersection(members):
            raise ValueError("Aynı model dosyası iki aileye ait olamaz.")
        covered.update(members)
        model_ids.add(model["id"])
        notices = model.get("notices", [])
        if not isinstance(notices, list) or any(not isinstance(notice, str) or ("license", notice) not in records for notice in notices):
            raise ValueError("Model katalog lisans bildirimi doğrulanamadı.")
        required_notices = {records[("model", member)]["license_file"] for member in members}
        model["notices"] = sorted(set(notices) | required_notices)
        if "gemma" in model["license"].casefold():
            gemma_notices = {"gemma-original-license", "gemma-terms", "gemma-prohibited-use", "gemma-notice", "ksi-model-terms"}
            if not gemma_notices <= set(model["notices"]):
                raise ValueError("Gemma modelinin koşulları, kullanım kısıtlamaları ve zorunlu bildirimi eksik.")
    if covered != known:
        raise ValueError("Bazı paket model dosyalarının katalog ailesi yok.")
    if ("support", "model-catalog") in records or "model-catalog.json" in seen:
        raise ValueError("Model kataloğunu mühürleyici üretir; tanım girdisi olamaz.")
    catalog_data = {"schema_version": 1, "models": models}
    encoded_catalog = (json.dumps(catalog_data, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(encoded_catalog) > 256 * 1024:
        raise ValueError("Model katalog boyutu sınırı aşıyor.")
    catalog = safe_member(resources, "model-catalog.json")
    manifest = safe_member(resources, "offline-manifest.json")
    if catalog.exists() or manifest.exists():
        raise FileExistsError("Mühürlenmiş paket dosyaları yeniden yazılmaz.")
    atomic_write_bytes(catalog, encoded_catalog, mode=0o644)
    entries.append(PayloadFile("model-catalog.json", digest_file(catalog), catalog.stat().st_size, "support", "model-catalog"))
    data = {"schema_version": 1, "architecture": architecture, "files": [asdict(entry) for entry in entries]}
    encoded_manifest = (json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(encoded_manifest) > MAX_MANIFEST_BYTES:
        raise ValueError("Paket manifesti boyut sınırı aşıyor.")
    atomic_write_bytes(manifest, encoded_manifest, mode=0o644)
    verified = OfflinePayload.load(resources, architecture=architecture)
    for entry in verified.files:
        verified.verify(entry)
    return {"architecture": architecture, "files": len(entries),
            "payload_bytes": sum(entry.size for entry in entries),
            "manifest_sha256": digest_file(manifest), "tested": False}
