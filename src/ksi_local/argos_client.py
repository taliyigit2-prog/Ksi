"""Direct offline translation backend using hash-verified bundled packages."""

from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

from ksi_local.atomic_files import atomic_write_json
from ksi_local.bundle_runtime import OfflinePayload, bundle_root, digest_file, safe_member
from ksi_local.engine_runner import run_engine


class ArgosClient:
    def __init__(self, packages: Path):
        resources = bundle_root()
        if resources is None:
            raise RuntimeError("Argos doğrulanmış çevrimdışı paket gerektirir.")
        self.packages = packages
        if packages.is_symlink() or not packages.is_dir():
            raise ValueError("Argos dil paketi klasörü geçersiz.")
        payload = OfflinePayload.load(resources)
        entries = [entry for entry in payload.files if entry.role == "model" and entry.path.startswith("models/argos/")]
        if not entries:
            raise RuntimeError("Çevrimdışı Argos dil paketleri bulunamadı.")
        expected_paths = set()
        for entry in entries:
            path = safe_member(packages, entry.path.removeprefix("models/argos/"))
            if not path.is_file() or path.stat().st_size != entry.size or digest_file(path) != entry.sha256:
                raise RuntimeError("Argos dil paketi bütünlük doğrulamasından geçmedi.")
            expected_paths.add(path)
        # The upstream package loader must not discover unverified user models.
        for path in packages.rglob("*"):
            if path.is_symlink() or (path.is_file() and path not in expected_paths):
                raise ValueError("Argos klasöründe doğrulanmamış paket dosyası var.")

    def translate_items(self, items, *, source_language, target_language, glossary=None):
        protected = {}
        if glossary is not None:
            protected.update({name: name for name in glossary.preserve})
            if target_language == "tr":
                protected.update(dict(glossary.terms))
        terms = sorted(protected, key=len, reverse=True)
        pattern = re.compile("(" + "|".join([r"VTRTOKEN[A-Za-z0-9]+X", *(re.escape(term) for term in terms)]) + ")")
        fragments, plans = [], []
        for item in items:
            plan = []
            for fragment in pattern.split(item["text"]):
                if not fragment:
                    continue
                if fragment in protected or re.fullmatch(r"VTRTOKEN[A-Za-z0-9]+X", fragment):
                    plan.append(protected.get(fragment, fragment))
                elif fragment.strip():
                    leading = fragment[:len(fragment) - len(fragment.lstrip())]
                    trailing = fragment[len(fragment.rstrip()):]
                    if leading:
                        plan.append(leading)
                    plan.append(len(fragments))
                    fragments.append(fragment.strip())
                    if trailing:
                        plan.append(trailing)
                else:
                    plan.append(fragment)
            plans.append(plan)
        translated = []
        if fragments:
            with tempfile.TemporaryDirectory(prefix="ksi-argos-") as temporary:
                stage = Path(temporary)
                request, response = stage / "request.json", stage / "result.json"
                atomic_write_json(request, {"operation": "translate", "packages": str(self.packages),
                    "cache": str(stage / "cache"), "source_language": source_language,
                    "target_language": target_language, "texts": fragments})
                run_engine([sys.executable, "-m", "ksi_local.local_ai_worker", str(request), str(response)], timeout=1800)
                if response.stat().st_size > 4 * 1024**2:
                    raise RuntimeError("Argos çıktı boyutu sınırı aşıyor.")
                result = json.loads(response.read_text(encoding="utf-8"))
                translated = result.get("texts")
                if not result.get("ok") or not isinstance(translated, list) or len(translated) != len(fragments) or any(not isinstance(value, str) for value in translated):
                    raise RuntimeError("Argos çeviri yanıtı doğrulanamadı.")
        records = [{"id": item["id"], "text": "".join(translated[value] if type(value) is int else value for value in plan)} for item, plan in zip(items, plans, strict=True)]
        return json.dumps({"translations": records}, ensure_ascii=False)
