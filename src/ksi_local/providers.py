"""GUI-independent contracts for the deliberately narrow provider surface."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Callable, Protocol

from ksi_local.downloader import DownloadPlan, Platform, ProbePlan, ValidatedSource, validate_source_url


class ProviderOperation(StrEnum):
    INSPECT = "inspect"
    AUTHORIZE = "authorize"
    DOWNLOAD = "download"
    PROGRESS = "progress"
    STOP = "stop"
    RESUME = "resume"


@dataclass(frozen=True)
class ProviderCapabilities:
    provider_id: str
    display_name: str
    operations: tuple[ProviderOperation, ...]
    authentication_optional: bool
    drm_supported: bool = False


@dataclass(frozen=True)
class ProviderProgress:
    downloaded_bytes: int
    total_bytes: int | None
    resumable: bool


class Provider(Protocol):
    capabilities: ProviderCapabilities

    def validate(self, raw_url: str) -> ValidatedSource: ...
    def inspect_plan(self, raw_url: str, **kwargs: object) -> ProbePlan: ...
    def download_plan(self, raw_url: str, output_directory: Path, **kwargs: object) -> DownloadPlan: ...
    def stop(self) -> None: ...
    def resume(self) -> None: ...


class RequestRateLimiter:
    """Monotonic per-provider limiter; waiting is injectable for deterministic tests."""

    def __init__(self, minimum_interval_seconds: float, *, clock: Callable[[], float] = time.monotonic, wait: Callable[[float], None] = time.sleep) -> None:
        if minimum_interval_seconds < 0:
            raise ValueError("İstek aralığı negatif olamaz.")
        self.minimum_interval_seconds = minimum_interval_seconds
        self._clock = clock
        self._wait = wait
        self._last_request: float | None = None

    def acquire(self) -> None:
        now = self._clock()
        if self._last_request is not None:
            remaining = self.minimum_interval_seconds - (now - self._last_request)
            if remaining > 0:
                self._wait(remaining)
                now = self._clock()
        self._last_request = now


PUBLIC_PROVIDER_CAPABILITIES: dict[Platform, ProviderCapabilities] = {
    Platform.YOUTUBE: ProviderCapabilities("youtube", "YouTube", tuple(ProviderOperation), False),
    Platform.X: ProviderCapabilities("x", "X", tuple(ProviderOperation), True),
    Platform.UDEMY: ProviderCapabilities("udemy", "Udemy (Deneysel)", tuple(ProviderOperation), True),
}


def capabilities_for_url(raw_url: str) -> ProviderCapabilities:
    return PUBLIC_PROVIDER_CAPABILITIES[validate_source_url(raw_url).platform]


def tested_public_providers() -> tuple[str, ...]:
    return tuple(item.display_name for item in PUBLIC_PROVIDER_CAPABILITIES.values())
