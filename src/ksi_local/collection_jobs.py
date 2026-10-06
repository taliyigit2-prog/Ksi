"""Resumable, sequential YouTube collection planning independent from Qt."""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from ksi_local.atomic_files import atomic_write_json
from ksi_local.downloader import Platform, ProbePlan, validate_source_url
from ksi_local.privacy import redact_sensitive_text
from ksi_local.storage import GIB, MIN_FREE_RESERVE_BYTES, conservative_video_mbps


COLLECTION_SCHEMA_VERSION = 1
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{3,128}$")
_ACTIVE_LIVE = {"is_live", "is_upcoming"}
_UNAVAILABLE = {"private", "deleted", "members_only", "age_restricted", "region_blocked"}


class CollectionItemStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class CollectionItem:
    video_id: str
    url: str
    title: str
    duration_seconds: float
    live_status: str | None = None
    availability: str = "public"
    selected: bool = True
    status: CollectionItemStatus = CollectionItemStatus.PENDING
    error: str | None = None


@dataclass(frozen=True)
class CollectionPlan:
    source_url: str
    items: tuple[CollectionItem, ...]
    total_duration_seconds: float
    estimated_source_bytes: int
    estimated_peak_bytes: int
    required_bytes: int
    free_bytes: int
    fits: bool
    active_live_count: int

    @property
    def selected_count(self) -> int:
        return sum(item.selected for item in self.items)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["items"] = [
            {**asdict(item), "status": item.status.value} for item in self.items
        ]
        return payload


def build_collection_probe_plan(
    raw_url: str, *, yt_dlp_path: str = "yt-dlp", js_runtime: tuple[str, str] | None = None
) -> ProbePlan:
    """Build an uncapped metadata-only YouTube collection probe."""
    source = validate_source_url(raw_url)
    if source.platform is not Platform.YOUTUBE:
        raise ValueError("Koleksiyon incelemesi yalnız YouTube kanal ve listelerini destekler.")
    argv = [
        yt_dlp_path,
        "--ignore-config",
        "--no-plugin-dirs",
        "--no-remote-components",
        "--skip-download",
        "--dump-single-json",
        "--no-cache-dir",
        "--socket-timeout",
        "20",
        "--yes-playlist",
    ]
    if js_runtime is not None:
        runtime_name, runtime_path = js_runtime
        if runtime_name not in {"deno", "node"}:
            raise ValueError("Desteklenen JS çalışma ortamları deno ve node'dur.")
        argv.extend(("--js-runtimes", f"{runtime_name}:{Path(runtime_path).expanduser().resolve()}"))
    argv.extend(("--", source.url))
    return ProbePlan(source, tuple(argv))


def _entry_url(entry: dict[str, Any], video_id: str) -> str:
    # Canonical construction avoids persisting tracking/authentication query data
    # returned by an extractor. Shorts remain ordinary videos in the queue.
    return f"https://www.youtube.com/watch?v={video_id}"


def _safe_collection_url(url: str) -> str:
    parsed = urlsplit(url)
    values = parse_qs(parsed.query)
    query = urlencode({"list": values["list"][0]}) if values.get("list") else ""
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))


def _availability(entry: dict[str, Any]) -> str:
    availability = str(entry.get("availability") or "public").casefold()
    message = " ".join(str(entry.get(key) or "") for key in ("title", "availability", "error"))
    lowered = message.casefold()
    if availability in _UNAVAILABLE:
        return availability
    markers = {
        "private": ("private video", "özel video"),
        "deleted": ("deleted video", "removed"),
        "members_only": ("members-only", "members only"),
        "age_restricted": ("age-restricted", "confirm your age"),
        "region_blocked": ("not available in your country", "geo restricted"),
    }
    return next((kind for kind, terms in markers.items() if any(term in lowered for term in terms)), availability)


def plan_collection(
    payload: dict[str, Any],
    *,
    source_url: str,
    free_bytes: int,
    selected_ids: Iterable[str] | None = None,
    completed_ids: Iterable[str] = (),
    max_height: int = 1080,
    processing_requested: bool = True,
    missing_model_bytes: int = 0,
    allow_active_live: bool = False,
    live_recording_limit_seconds: int | None = None,
) -> CollectionPlan:
    """Normalize yt-dlp collection JSON and calculate one queue-wide disk budget."""
    source = validate_source_url(source_url)
    if source.platform is not Platform.YOUTUBE:
        raise ValueError("Koleksiyon kaynağı YouTube olmalıdır.")
    if free_bytes < 0 or missing_model_bytes < 0:
        raise ValueError("Disk ve model boyutları negatif olamaz.")
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list):
        raw_entries = [payload]
    wanted = set(selected_ids) if selected_ids is not None else None
    already_completed = set(completed_ids)
    seen: set[str] = set()
    items: list[CollectionItem] = []
    active_count = 0

    for raw in raw_entries:
        if not isinstance(raw, dict):
            continue
        video_id = str(raw.get("id") or "").strip()
        if not _VIDEO_ID.fullmatch(video_id) or video_id in seen:
            continue
        seen.add(video_id)
        selected = wanted is None or video_id in wanted
        live_status = str(raw.get("live_status") or "").casefold() or None
        availability = _availability(raw)
        duration_value = raw.get("duration")
        duration = float(duration_value) if isinstance(duration_value, (int, float)) else 0.0
        status = CollectionItemStatus.PENDING
        error = None
        if video_id in already_completed:
            status = CollectionItemStatus.SKIPPED
            error = "Bu video kimliği önceki kanal güncellemesinde tamamlandı."
        elif availability in _UNAVAILABLE:
            status = CollectionItemStatus.FAILED
            error = "Video tekil erişim kısıtı nedeniyle işlenemiyor."
        elif live_status in _ACTIVE_LIVE:
            active_count += int(selected)
            if not allow_active_live:
                status = CollectionItemStatus.SKIPPED
                error = "Devam eden canlı yayın için açık kayıt onayı gerekir."
            elif not live_recording_limit_seconds or live_recording_limit_seconds <= 0:
                raise ValueError("Canlı yayın için pozitif güvenli durdurma süresi gerekir.")
            else:
                duration = float(live_recording_limit_seconds)
        if duration <= 0 and status is CollectionItemStatus.PENDING and selected:
            status = CollectionItemStatus.FAILED
            error = "Video süresi belirlenemedi; diğer kuyruk öğeleri devam edebilir."
        items.append(
            CollectionItem(
                video_id=video_id,
                url=_entry_url(raw, video_id),
                title=str(raw.get("title") or video_id)[:300],
                duration_seconds=max(0.0, duration),
                live_status=live_status,
                availability=availability,
                selected=selected,
                status=status,
                error=error,
            )
        )

    selected_items = [item for item in items if item.selected and item.status is CollectionItemStatus.PENDING]
    total_duration = sum(item.duration_seconds for item in selected_items)
    bytes_per_second = (conservative_video_mbps(max_height) + 0.192) * 1_000_000 / 8
    source_bytes = math.ceil(total_duration * bytes_per_second)
    if processing_requested:
        audio_work = math.ceil(total_duration * 192_000 * 2)
        peak_bytes = math.ceil(source_bytes * 2.25 + audio_work + (2 * GIB if items else 0))
    else:
        peak_bytes = math.ceil(source_bytes * 1.35 + (1 * GIB if items else 0))
    required = peak_bytes + missing_model_bytes + MIN_FREE_RESERVE_BYTES
    return CollectionPlan(
        source_url=_safe_collection_url(source.url),
        items=tuple(items),
        total_duration_seconds=total_duration,
        estimated_source_bytes=source_bytes,
        estimated_peak_bytes=peak_bytes,
        required_bytes=required,
        free_bytes=free_bytes,
        fits=free_bytes >= required,
        active_live_count=active_count,
    )


class CollectionState:
    """Crash-safe collection checkpoint; source and completed outputs are never removed."""

    def __init__(self, path: str | Path, plan: CollectionPlan) -> None:
        self.path = Path(path)
        self.plan = plan

    def save(self) -> None:
        atomic_write_json(
            self.path,
            {"schema_version": COLLECTION_SCHEMA_VERSION, "plan": self.plan.to_dict()},
        )

    @classmethod
    def load(cls, path: str | Path) -> CollectionState:
        target = Path(path)
        payload = json.loads(target.read_text(encoding="utf-8"))
        if payload.get("schema_version") != COLLECTION_SCHEMA_VERSION:
            raise ValueError("Koleksiyon durum şeması desteklenmiyor.")
        raw_plan = payload.get("plan")
        if not isinstance(raw_plan, dict) or not isinstance(raw_plan.get("items"), list):
            raise ValueError("Koleksiyon durum dosyası geçersiz.")
        items = tuple(
            CollectionItem(
                video_id=str(item["video_id"]),
                url=str(item["url"]),
                title=str(item["title"]),
                duration_seconds=float(item["duration_seconds"]),
                live_status=item.get("live_status"),
                availability=str(item.get("availability", "public")),
                selected=bool(item.get("selected", True)),
                status=CollectionItemStatus(item["status"]),
                error=item.get("error"),
            )
            for item in raw_plan["items"]
        )
        plan = CollectionPlan(
            source_url=str(raw_plan["source_url"]),
            items=items,
            total_duration_seconds=float(raw_plan["total_duration_seconds"]),
            estimated_source_bytes=int(raw_plan["estimated_source_bytes"]),
            estimated_peak_bytes=int(raw_plan["estimated_peak_bytes"]),
            required_bytes=int(raw_plan["required_bytes"]),
            free_bytes=int(raw_plan["free_bytes"]),
            fits=bool(raw_plan["fits"]),
            active_live_count=int(raw_plan.get("active_live_count", 0)),
        )
        return cls(target, plan)

    def claim_next(self) -> CollectionItem | None:
        if any(item.status is CollectionItemStatus.RUNNING for item in self.plan.items):
            return None
        for index, item in enumerate(self.plan.items):
            if item.selected and item.status is CollectionItemStatus.PENDING:
                return self._update(index, CollectionItemStatus.RUNNING)
        return None

    def complete(self, video_id: str) -> CollectionItem:
        return self._transition(video_id, CollectionItemStatus.COMPLETED)

    def fail(self, video_id: str, error: str) -> CollectionItem:
        safe_error = redact_sensitive_text(error).strip()[:500]
        return self._transition(video_id, CollectionItemStatus.FAILED, error=safe_error)

    def recover_interrupted(self) -> int:
        recovered = 0
        updated = list(self.plan.items)
        for index, item in enumerate(updated):
            if item.status is CollectionItemStatus.RUNNING:
                updated[index] = replace(item, status=CollectionItemStatus.PENDING, error=None)
                recovered += 1
        if recovered:
            self.plan = replace(self.plan, items=tuple(updated))
            self.save()
        return recovered

    def _transition(
        self, video_id: str, status: CollectionItemStatus, *, error: str | None = None
    ) -> CollectionItem:
        for index, item in enumerate(self.plan.items):
            if item.video_id == video_id:
                if item.status is not CollectionItemStatus.RUNNING:
                    raise ValueError("Yalnız çalışan koleksiyon öğesi sonuçlandırılabilir.")
                return self._update(index, status, error=error)
        raise KeyError(video_id)

    def _update(
        self, index: int, status: CollectionItemStatus, *, error: str | None = None
    ) -> CollectionItem:
        updated = list(self.plan.items)
        updated[index] = replace(updated[index], status=status, error=error)
        self.plan = replace(self.plan, items=tuple(updated))
        self.save()
        return updated[index]
