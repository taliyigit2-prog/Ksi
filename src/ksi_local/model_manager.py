"""Manifest-bound offline model inventory; no inferred downloads or cloud APIs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ksi_local.bundle_runtime import OfflinePayload, digest_file, safe_member


@dataclass(frozen=True)
class ModelInventory:
    identifier: str
    title: str
    description: str
    license: str
    total_bytes: int
    installed_bytes: int
    state: str


class ModelManager:
    def __init__(self, resources: Path, installed: Path):
        if resources.is_symlink() or installed.is_symlink():
            raise ValueError("Model kaynak ve hedef kökleri symlink olamaz.")
        self.payload = OfflinePayload.load(resources)
        self.installed = installed
        catalog = self.payload.component("support", "model-catalog")
        if catalog.stat().st_size > 256 * 1024:
            raise ValueError("Model katalog boyutu sınırı aşıyor.")
        data = json.loads(catalog.read_text(encoding="utf-8"))
        if data.get("schema_version") != 1 or not isinstance(data.get("models"), list):
            raise ValueError("Model katalog sürümü desteklenmiyor.")
        if not 1 <= len(data["models"]) <= 64:
            raise ValueError("Model katalog sayısı geçersiz.")
        self.specifications = {}
        self.entries = {entry.identifier: entry for entry in self.payload.files if entry.role == "model"}
        for model in data["models"]:
            if not isinstance(model, dict) or not all(isinstance(model.get(key), str) and 0 < len(model[key]) <= 500 for key in ("id", "title", "description", "license")):
                raise ValueError("Model katalog açıklaması geçersiz.")
            members = model.get("members")
            if not isinstance(members, list) or not members or any(not isinstance(member, str) for member in members) or len(set(members)) != len(members):
                raise ValueError("Model katalog dosya listesi geçersiz.")
            if any(member not in self.entries for member in members) or model["id"] in self.specifications:
                raise ValueError("Model katalog bileşeni bilinmiyor veya çakışıyor.")
            self.specifications[model["id"]] = model

    def license_text(self, identifier: str) -> str:
        """Read only manifest-bound offline notices, never arbitrary local files."""
        spec = self.specifications.get(identifier)
        if spec is None:
            raise ValueError("Model lisans kaydı bulunamadı.")
        notices = spec.get("notices")
        if not isinstance(notices, list) or not 1 <= len(notices) <= 64 or any(not isinstance(value, str) for value in notices):
            raise RuntimeError("Modelin çevrimdışı lisans metni pakette bulunamadı.")
        results, total = [], 0
        for identifier in dict.fromkeys(notices):
            entry = next((row for row in self.payload.files if row.role == "license" and row.identifier == identifier), None)
            if entry is None or entry.size > 256 * 1024:
                raise ValueError("Model lisans dosyası eksik veya boyut sınırını aşıyor.")
            total += entry.size
            if total > 2 * 1024**2:
                raise ValueError("Model lisans metinleri toplam boyut sınırını aşıyor.")
            verified = self.payload.verify(entry)
            results.append(identifier + "\n\n" + verified.read_text(encoding="utf-8"))
        return "\n\n────────\n\n".join(results)

    def inventory(self, *, verify: bool = False) -> tuple[ModelInventory, ...]:
        result = []
        for identifier, spec in self.specifications.items():
            total = installed = 0
            state = "verified" if verify else "installed"
            for member in spec["members"]:
                entry = self.entries[member]
                if not entry.path.startswith("models/"):
                    raise ValueError("Model katalog dosyası models dizininde değil.")
                target = safe_member(self.installed, entry.path.removeprefix("models/"))
                total += entry.size
                if not target.is_file():
                    state = "missing"
                elif target.stat().st_size != entry.size:
                    if state != "missing":
                        state = "corrupt"
                elif verify and digest_file(target) != entry.sha256:
                    if state != "missing":
                        state = "corrupt"
                else:
                    installed += entry.size
            result.append(ModelInventory(identifier, spec["title"], spec["description"], spec["license"], total, installed, state))
        return tuple(result)

    def install(self):
        self.payload.install_models(self.installed)
        return self.inventory(verify=True)
