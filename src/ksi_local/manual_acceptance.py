"""Privacy-safe, resumable human acceptance ledger for the final release gate."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ksi_local import __version__
from ksi_local.atomic_files import atomic_write_json
from ksi_local.privacy import redact_sensitive_text
from ksi_local.project_metadata import PRODUCT_NAME
from ksi_local.translation_targets import TRANSLATEGEMMA_CANDIDATE_LOCALES


STATUS_VALUES = frozenset({"pending", "passed", "failed"})
QUALITY_FIELDS = frozenset(
    {"meaning", "naturalness", "pronunciation", "ui", "visual", "usability"}
)


@dataclass(frozen=True)
class AcceptanceCase:
    key: str
    title: str
    instructions: str
    ratings: tuple[str, ...] = ()
    network_required: bool = False
    blocking: bool = True


CORE_CASES = (
    AcceptanceCase("app-reopen", "Uygulamayı kapatıp yeniden açma", "Simgeden açın, normal kapatın ve yeniden açın.", ("ui", "usability")),
    AcceptanceCase("youtube-video", "Tek YouTube videosu", "Erişim hakkınız olan kısa bir videoda altyazı, özet ve final videoyu üretin.", ("meaning", "naturalness", "ui", "visual"), True),
    AcceptanceCase("youtube-shorts", "YouTube Shorts", "Kısa dikey videoyu işleyip görüntü yönünün korunduğunu denetleyin.", ("meaning", "ui", "visual"), True),
    AcceptanceCase("x-public", "Herkese açık X gönderisi", "Çerez kullanmadan herkese açık bir video gönderisini işleyin.", ("meaning", "ui", "visual"), True),
    AcceptanceCase("completed-live", "Tamamlanmış canlı yayın", "Üç saat sınırının altındaki tamamlanmış bir yayını ön inceleyin.", ("ui", "usability"), True),
    AcceptanceCase("youtube-channel", "YouTube kanal seçimi", "Kanalı inceleyip yalnız seçtiğiniz öğeleri kuyruğa ekleyin.", ("ui", "usability"), True),
    AcceptanceCase("youtube-playlist", "YouTube oynatma listesi", "Listeyi inceleyip seçim, durdurma ve devam davranışını kontrol edin.", ("ui", "usability"), True),
    AcceptanceCase("document", "Yerel belge", "Kısa bir belgeyi çevirin, özetleyin ve çıktılarını açın.", ("meaning", "naturalness", "ui", "visual")),
    AcceptanceCase("web-article", "Web makalesi", "Robots politikasına izin veren bir makaleyi açık ağ onayıyla işleyin.", ("meaning", "naturalness", "ui", "visual"), True),
    AcceptanceCase("dubbing", "Türkçe dublaj", "Kısa videoda dublaj üretip dudak değil zaman uyumu, telaffuz ve ses seviyesini dinleyin.", ("meaning", "naturalness", "pronunciation")),
    AcceptanceCase("subtitle-video", "Seçilebilir altyazılı final video", "Final videoda Türkçe altyazıyı açıp kapatın; ses ve görüntünün yeniden kodlanmadığını kontrol edin.", ("meaning", "visual")),
    AcceptanceCase("summary", "Kanıtlı özet", "Özet maddelerini zaman kodları ve kaynak metinle karşılaştırın.", ("meaning", "naturalness")),
    AcceptanceCase("image", "Görsel araçları", "Yeniden boyutlandırma, arka plan kaldırma ve geri alma önizlemelerini kontrol edin.", ("ui", "visual", "usability")),
    AcceptanceCase("storage-relocation", "Depolama taşıma", "Ön izleme sonrası taşıyın; kaynak ve önceki çıktının yerinde kaldığını doğrulayın.", ("ui", "usability")),
    AcceptanceCase("ssd-disconnect", "SSD çıkarma ve yeniden bağlama", "Çalışan işte SSD'yi güvenli çıkarın, uyarıyı görün ve aynı SSD ile devam edin.", ("ui", "usability")),
    AcceptanceCase("network-disconnect", "Ağ kesintisi", "Yalnız indirme sırasında ağı kapatın; yerel işlemlerin ağ istemediğini doğrulayın.", ("ui", "usability"), True),
    AcceptanceCase("stop-resume", "Durdurma ve devam", "Uzun bir işi durdurun, tamamlanmış aşamaları denetleyip yeniden başlatın.", ("ui", "usability")),
    AcceptanceCase("keyboard", "Klavye ve VoiceOver", "Tab/Shift-Tab, oklar, Enter, Space ve Cmd+C ile ana akışı gezin.", ("ui", "usability")),
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def acceptance_cases() -> tuple[AcceptanceCase, ...]:
    pilots = tuple(
        AcceptanceCase(
            f"locale-{locale.casefold()}",
            f"TranslateGemma aday yerel ayarı: {locale}",
            "Korunan sayıları/özel adları ve anlamı değerlendirin; bu kayıt tek başına dili ürün desteği yapmaz.",
            ("meaning", "naturalness"),
            blocking=False,
        )
        for locale in TRANSLATEGEMMA_CANDIDATE_LOCALES
    )
    return (*CORE_CASES, *pilots)


def create_session(path: str | Path) -> dict[str, Any]:
    unresolved = Path(path).expanduser()
    if unresolved.is_symlink():
        raise FileExistsError("Kabul oturumu hedefi yeni olmalıdır; mevcut kayıt üzerine yazılmaz.")
    target = unresolved.resolve()
    if target.exists():
        raise FileExistsError("Kabul oturumu hedefi yeni olmalıdır; mevcut kayıt üzerine yazılmaz.")
    cases = {
        item.key: {
            "title": item.title,
            "instructions": item.instructions,
            "ratings_required": list(item.ratings),
            "network_required": item.network_required,
            "blocking": item.blocking,
            "status": "pending",
            "ratings": {},
            "note": "",
            "artifact_sha256": None,
            "updated_at": None,
        }
        for item in acceptance_cases()
    }
    payload: dict[str, Any] = {
        "schema_version": 1,
        "product": PRODUCT_NAME,
        "app_version": __version__,
        "created_at": _now(),
        "privacy": "Kaynak URL, hesap bilgisi, dosya yolu, çerez veya kullanıcı içeriği kaydedilmez.",
        "cases": cases,
        "failures": [],
    }
    atomic_write_json(target, payload)
    return payload


def load_session(path: str | Path) -> dict[str, Any]:
    unresolved = Path(path).expanduser()
    if unresolved.is_symlink():
        raise ValueError("Kabul oturumu sembolik bağlantı olamaz.")
    target = unresolved.resolve()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("Kabul oturumu okunamıyor.") from error
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("Kabul oturumu şeması desteklenmiyor.")
    if payload.get("product") != PRODUCT_NAME or not isinstance(payload.get("cases"), dict):
        raise ValueError("Kabul oturumu KSI Local Studio ile eşleşmiyor.")
    return payload


def _safe_text(value: str, *, limit: int = 2000) -> str:
    safe = redact_sensitive_text(value).replace(str(Path.home()), "[kullanıcı-klasörü]")
    safe = re.sub(
        r"/Volumes/[^\s,;:'\"<>]+(?:/[^\s,;:'\"<>]+)*",
        "[harici-disk-yolu]",
        safe,
    )
    return safe[:limit]


def record_result(
    path: str | Path,
    case_key: str,
    *,
    status: str,
    ratings: dict[str, int] | None = None,
    note: str = "",
    artifact_sha256: str | None = None,
    expected: str = "",
    actual: str = "",
    reproduction_steps: tuple[str, ...] = (),
) -> dict[str, Any]:
    if status not in STATUS_VALUES:
        raise ValueError("Kabul durumu pending, passed veya failed olmalıdır.")
    payload = load_session(path)
    cases = payload["cases"]
    if case_key not in cases:
        raise KeyError(f"Kabul maddesi bulunamadı: {case_key}")
    case = cases[case_key]
    scores = ratings or {}
    if any(key not in QUALITY_FIELDS for key in scores):
        raise ValueError("Bilinmeyen kalite puanı alanı.")
    if any(isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5 for value in scores.values()):
        raise ValueError("Kalite puanları 1–5 arasında tam sayı olmalıdır.")
    required = set(case["ratings_required"])
    if status == "passed" and (not required.issubset(scores) or any(scores[key] < 4 for key in required)):
        raise ValueError("Geçti sonucu için gerekli bütün insan puanları en az 4/5 olmalıdır.")
    if artifact_sha256 is not None:
        digest = artifact_sha256.casefold()
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("Çıktı kanıtı geçerli bir SHA-256 olmalıdır.")
        artifact_sha256 = digest
    timestamp = _now()
    case.update(
        status=status,
        ratings=scores,
        note=_safe_text(note),
        artifact_sha256=artifact_sha256,
        updated_at=timestamp,
    )
    if status == "failed":
        details = "\n".join((case_key, expected, actual, *reproduction_steps, timestamp))
        payload.setdefault("failures", []).append(
            {
                "id": "KSI-ACCEPT-" + hashlib.sha256(details.encode()).hexdigest()[:12].upper(),
                "case": case_key,
                "expected": _safe_text(expected),
                "actual": _safe_text(actual),
                "reproduction_steps": [_safe_text(step, limit=500) for step in reproduction_steps[:12]],
                "created_at": timestamp,
            }
        )
    unresolved = Path(path).expanduser()
    if unresolved.is_symlink():
        raise ValueError("Kabul oturumu sembolik bağlantı olamaz.")
    atomic_write_json(unresolved.resolve(), payload)
    return payload


@dataclass(frozen=True)
class AcceptanceProgress:
    blocking_total: int
    blocking_passed: int
    blocking_failed: int
    blocking_pending: int
    research_total: int
    research_recorded: int
    releasable: bool


def session_progress(path: str | Path) -> AcceptanceProgress:
    payload = load_session(path)
    blocking = [case for case in payload["cases"].values() if case["blocking"]]
    research = [case for case in payload["cases"].values() if not case["blocking"]]
    passed = sum(case["status"] == "passed" for case in blocking)
    failed = sum(case["status"] == "failed" for case in blocking)
    pending = sum(case["status"] == "pending" for case in blocking)
    return AcceptanceProgress(
        blocking_total=len(blocking),
        blocking_passed=passed,
        blocking_failed=failed,
        blocking_pending=pending,
        research_total=len(research),
        research_recorded=sum(case["status"] != "pending" for case in research),
        releasable=bool(blocking) and passed == len(blocking) and failed == 0,
    )


def progress_dict(path: str | Path) -> dict[str, Any]:
    return asdict(session_progress(path))
