"""Unified, read-only inspection for local files and supported video URLs."""

from __future__ import annotations

import math
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ksi_local.document_security import (
    DOCUMENT_SUFFIXES,
    DocumentInspection,
    inspect_document_isolated,
)
from ksi_local.downloader import (
    BrowserSession,
    MAX_DOWNLOAD_HEIGHT,
    Platform,
    build_probe_plan,
    estimate_download_bytes,
    run_probe_with_retry,
    summarize_probe,
    validate_source_url,
)
from ksi_local.media import MEDIA_SUFFIXES, probe_local_media
from ksi_local.settings import WorkspacePaths
from ksi_local.storage import (
    DEFAULT_MISSING_MODEL_BUDGET_BYTES,
    GIB,
    StorageBudget,
    conservative_video_mbps,
    estimate_video_storage,
)
from ksi_local.subtitles import TEXT_SOURCE_SUFFIXES, read_text_source
from ksi_local.tool_integrity import verify_download_tools, verify_local_probe_tool


@dataclass(frozen=True)
class FormatChoice:
    height: int
    estimated_download_bytes: int
    estimated_peak_bytes: int
    required_bytes: int
    fits: bool


@dataclass(frozen=True)
class MediaChoice:
    index: int
    media_id: str
    title: str
    uploader: str | None
    duration_seconds: float
    manual_subtitles: tuple[str, ...]
    automatic_subtitles: tuple[str, ...]
    detected_language: str | None
    available_heights: tuple[int, ...]
    default_height: int
    format_choices: tuple[FormatChoice, ...]


@dataclass(frozen=True)
class PreflightResult:
    source_kind: str
    platform: str
    title: str
    uploader: str | None
    duration_seconds: float
    live_status: str | None
    entry_count: int
    manual_subtitles: tuple[str, ...]
    automatic_subtitles: tuple[str, ...]
    detected_language: str | None
    requires_authentication: bool
    available_heights: tuple[int, ...]
    default_height: int | None
    format_choices: tuple[FormatChoice, ...]
    fixed_budget: FormatChoice | None
    requested_outputs: tuple[str, ...]
    free_bytes: int
    retry_count: int
    warnings: tuple[str, ...]
    tool_versions: tuple[str, ...]
    media_choices: tuple[MediaChoice, ...] = ()
    session_used: bool = False
    document: DocumentInspection | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["format_choices"] = [asdict(item) for item in self.format_choices]
        return payload

    def media_choice(self, media_index: int = 1) -> MediaChoice | None:
        return next((item for item in self.media_choices if item.index == media_index), None)

    def choice(
        self, height: int | None = None, *, media_index: int = 1
    ) -> FormatChoice | None:
        media = self.media_choice(media_index)
        choices = media.format_choices if media is not None else self.format_choices
        target = (
            media.default_height if media is not None else self.default_height
        ) if height is None else height
        return next((item for item in choices if item.height == target), None)


def _timestamp_seconds(value: str) -> float:
    hours, minutes, rest = value.split(":")
    seconds, milliseconds = rest.split(",")
    return (
        int(hours) * 3600
        + int(minutes) * 60
        + int(seconds)
        + int(milliseconds) / 1000
    )


def _output_names(
    *,
    download_only: bool,
    want_subtitle: bool,
    want_summary: bool,
    want_dub: bool = False,
    document: bool = False,
) -> tuple[str, ...]:
    if download_only:
        return ("Sadece indir",)
    selected: list[str] = []
    if want_subtitle:
        selected.append("Türkçe belge çevirisi" if document else "Türkçe altyazı")
    if want_summary:
        selected.append("Türkçe özet")
    if want_dub:
        selected.append("Türkçe dublaj")
    if not selected:
        raise ValueError("En az bir çıktı türü seçilmelidir.")
    return tuple(selected)


def _resolved_job_kind(source: str, requested: str) -> str:
    if requested not in {"auto", "video", "document"}:
        raise ValueError("İş türü otomatik, video veya belge olmalıdır.")
    is_url = source.casefold().startswith("https://")
    if requested == "document" and is_url:
        raise ValueError("Belge işi için bilgisayardaki bir dosyayı seçin.")
    if requested != "auto":
        return requested
    if is_url:
        return "video"
    return "document" if Path(source).suffix.casefold() in DOCUMENT_SUFFIXES else "video"


def _inspect_document(
    path: Path,
    *,
    outputs: tuple[str, ...],
    free_bytes: int,
) -> PreflightResult:
    inspection = inspect_document_isolated(path)
    estimated_peak = max(inspection.size_bytes * 2, 64 * 1024**2)
    required = estimated_peak + 20 * GIB
    fits = free_bytes >= required and inspection.accepted
    warnings = [*inspection.warnings]
    if inspection.blocking_reasons:
        warnings.extend(f"ENGELLENDİ: {item}" for item in inspection.blocking_reasons)
    if free_bytes < required:
        warnings.append("disk'de belge kopyası ve 20 GiB güvenlik payı için alan yetersiz.")
    return PreflightResult(
        source_kind="document",
        platform="Yerel belge",
        title=inspection.filename,
        uploader=None,
        duration_seconds=0,
        live_status=None,
        entry_count=1,
        manual_subtitles=(),
        automatic_subtitles=(),
        detected_language=inspection.detected_language,
        requires_authentication=False,
        available_heights=(),
        default_height=None,
        format_choices=(),
        fixed_budget=FormatChoice(
            height=0,
            estimated_download_bytes=inspection.size_bytes,
            estimated_peak_bytes=estimated_peak,
            required_bytes=required,
            fits=fits,
        ),
        requested_outputs=outputs,
        free_bytes=free_bytes,
        retry_count=0,
        warnings=tuple(warnings),
        tool_versions=("KSI Local Studio güvenli belge işçisi",),
        document=inspection,
    )


def _missing_model_budget(
    workspace: WorkspacePaths, processing_requested: bool, *, want_dub: bool = False
) -> int:
    if not processing_requested:
        return 0
    from ksi_local.bundle_runtime import bundle_root, host_architecture
    cpu = bundle_root() is not None and host_architecture() == "x86_64"
    tts_ready = ((workspace.root / "models/tts/piper/tr_TR-fettah-medium.onnx").is_file()
                 and (workspace.root / "models/tts/piper/tr_TR-fettah-medium.onnx.json").is_file()) if cpu else (workspace.root / "models/tts/chatterbox-multilingual-v3").is_dir()
    whisper_ready = workspace.models_whisper.is_file() if cpu else workspace.models_whisper.is_dir()
    if (
        workspace.models_ollama.is_dir()
        and whisper_ready
        and (not want_dub or tts_ready)
    ):
        return 0
    return DEFAULT_MISSING_MODEL_BUDGET_BYTES


def _budget_for(
    *,
    duration: float,
    free_bytes: int,
    height: int,
    known_source_bytes: int | None,
    missing_model_bytes: int,
    processing_requested: bool,
) -> StorageBudget:
    return estimate_video_storage(
        duration,
        free_bytes=free_bytes,
        missing_model_bytes=missing_model_bytes,
        max_video_mbps=conservative_video_mbps(height),
        source_bytes=known_source_bytes,
        processing_requested=processing_requested,
    )


def _inspect_subtitle(
    path: Path,
    *,
    outputs: tuple[str, ...],
    free_bytes: int,
) -> PreflightResult:
    cues = read_text_source(path)
    duration = _timestamp_seconds(cues[-1].end)
    if duration <= 0 or duration > 3 * 60 * 60:
        raise ValueError("Altyazı süresi sıfırdan büyük ve en fazla üç saat olmalıdır.")
    return PreflightResult(
        source_kind="subtitle",
        platform="Yerel konuşma metni" if path.suffix.casefold() == ".txt" else "Yerel altyazı",
        title=path.name,
        uploader=None,
        duration_seconds=duration,
        live_status=None,
        entry_count=1,
        manual_subtitles=(),
        automatic_subtitles=(),
        detected_language=None,
        requires_authentication=False,
        available_heights=(),
        default_height=None,
        format_choices=(),
        fixed_budget=None,
        requested_outputs=outputs,
        free_bytes=free_bytes,
        retry_count=0,
        warnings=(
            (
                "TXT kaynağında gerçek zaman kodu yoktur; özet izlenebilirliği için "
                "yaklaşık zamanlar üretilecektir."
                if path.suffix.casefold() == ".txt"
                else "Bu kaynak yalnız altyazı metni içerir; video indirilmez."
            ),
        ),
        tool_versions=(),
    )


def inspect_source(
    raw_source: str,
    *,
    workspace: WorkspacePaths,
    download_only: bool,
    want_subtitle: bool,
    want_summary: bool,
    want_dub: bool = False,
    ffmpeg_path: str | None = None,
    ffprobe_path: str | None = None,
    browser_session: BrowserSession | None = None,
    udemy_access_confirmed: bool = False,
    job_kind: str = "video",
) -> PreflightResult:
    """Inspect without downloading media and calculate every selectable disk budget."""
    source = raw_source.strip()
    if not source:
        raise ValueError("Kaynak boş olamaz.")
    resolved_kind = _resolved_job_kind(source, job_kind)
    outputs = _output_names(
        download_only=download_only,
        want_subtitle=want_subtitle,
        want_summary=want_summary,
        want_dub=want_dub,
        document=resolved_kind == "document",
    )
    processing_requested = not download_only
    summary_only = want_summary and not want_subtitle and not want_dub and not download_only
    free_bytes = shutil.disk_usage(workspace.root).free

    if resolved_kind == "document":
        if download_only:
            raise ValueError("Bilgisayardaki belge için ‘Sadece indir’ kullanılamaz.")
        if want_dub:
            raise ValueError("Belge işinde dublaj kullanılamaz.")
        path = Path(source).expanduser()
        if not path.is_file() and not path.is_symlink():
            raise FileNotFoundError("Yerel belge dosyası bulunamadı.")
        return _inspect_document(path, outputs=outputs, free_bytes=free_bytes)

    if not source.casefold().startswith("https://"):
        path = Path(source).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError("Yerel kaynak dosyası bulunamadı.")
        if path.suffix.casefold() in TEXT_SOURCE_SUFFIXES:
            if download_only:
                raise ValueError(
                    "Metin dosyası için ‘Sadece indir’ seçeneği kullanılamaz."
                )
            if want_dub:
                raise ValueError("Dublaj için metin yanında bir video dosyası gereklidir.")
            return _inspect_subtitle(path, outputs=outputs, free_bytes=free_bytes)
        if download_only:
            raise ValueError(
                "Yerel video zaten bilgisayarınızda; Türkçe altyazı veya özet seçin."
            )
        if path.suffix.casefold() not in MEDIA_SUFFIXES:
            raise ValueError("Desteklenen bir video, SRT, VTT veya TXT dosyası seçin.")
        verified_ffprobe = verify_local_probe_tool(ffprobe_path)
        media = probe_local_media(str(path), ffprobe_path=verified_ffprobe.path)
        if media["video_stream_count"] < 1:
            raise RuntimeError("Yerel dosyada video akışı bulunamadı.")
        if processing_requested and media["audio_stream_count"] < 1:
            raise RuntimeError(
                "Altyazı veya özet için yerel dosyada ses akışı bulunmalıdır."
            )
        if not media["within_mvp_duration"]:
            raise RuntimeError(
                "Video süresi sıfırdan büyük ve en fazla üç saat olmalıdır."
            )
        dimensions = media.get("dimensions") or []
        height = max((int(item[1]) for item in dimensions), default=0)
        duration = float(media["duration_seconds"])
        budget = _budget_for(
            duration=duration,
            free_bytes=free_bytes,
            height=min(max(height, 144), MAX_DOWNLOAD_HEIGHT),
            known_source_bytes=int(media["size_bytes"]),
            missing_model_bytes=_missing_model_budget(
                workspace, processing_requested, want_dub=want_dub
            ),
            processing_requested=processing_requested,
        )
        if not budget.fits:
            raise RuntimeError("Yerel video için disk güvenlik payı yetersiz.")
        return PreflightResult(
            source_kind="file",
            platform="Yerel video",
            title=path.name,
            uploader=None,
            duration_seconds=duration,
            live_status=None,
            entry_count=1,
            manual_subtitles=(),
            automatic_subtitles=(),
            detected_language=None,
            requires_authentication=False,
            available_heights=(height,) if height else (),
            default_height=None,
            format_choices=(),
            fixed_budget=FormatChoice(
                height=0,
                estimated_download_bytes=budget.estimated_source_bytes,
                estimated_peak_bytes=budget.estimated_peak_work_bytes,
                required_bytes=budget.required_bytes,
                fits=budget.fits,
            ),
            requested_outputs=outputs,
            free_bytes=free_bytes,
            retry_count=0,
            warnings=(),
            tool_versions=(f"ffprobe {verified_ffprobe.expected_version}",),
        )

    validated = validate_source_url(source)
    if browser_session is not None and validated.platform not in {Platform.X, Platform.UDEMY}:
        raise ValueError(
            "Ayrı tarayıcı oturumu yalnız X veya Udemy bağlantılarında kullanılabilir."
        )
    tools = verify_download_tools(
        yt_dlp_path=workspace.yt_dlp,
        deno_path=workspace.deno,
        ffmpeg_path=ffmpeg_path,
        ffprobe_path=ffprobe_path,
    )
    plan = build_probe_plan(
        validated.url,
        yt_dlp_path=str(workspace.yt_dlp),
        js_runtime=("deno", str(workspace.deno)),
        browser_session=browser_session,
        udemy_access_confirmed=udemy_access_confirmed,
    )
    payload, attempts = run_probe_with_retry(plan)
    missing_models = _missing_model_budget(
        workspace, processing_requested, want_dub=want_dub
    )

    raw_entries = payload.get("entries")
    if validated.platform is Platform.X and isinstance(raw_entries, list):
        media_payloads = [item for item in raw_entries if isinstance(item, dict)]
    else:
        media_payloads = [payload]
    if not media_payloads:
        raise RuntimeError("Gönderide indirilebilir video bulunamadı.")
    if validated.platform in {Platform.YOUTUBE, Platform.UDEMY} and len(media_payloads) != 1:
        raise RuntimeError("Tek bir video dersi seçilmelidir.")

    built_media: list[MediaChoice] = []
    media_metadata: list[dict[str, Any]] = []
    for index, media_payload in enumerate(media_payloads, start=1):
        metadata = summarize_probe(media_payload)
        duration_value = metadata.get("duration_seconds")
        if not isinstance(duration_value, (int, float)) or not 0 < duration_value <= 3 * 60 * 60:
            raise RuntimeError(
                f"{index}. videonun süresi okunamadı veya üç saat sınırını aşıyor."
            )
        live_status = str(metadata.get("live_status") or "") or None
        if live_status in {"is_live", "is_upcoming", "post_live"}:
            raise RuntimeError(
                "Canlı veya henüz tamamlanmamış yayınlar bu sürümde desteklenmiyor."
            )
        availability = str(metadata.get("availability") or "").casefold()
        if availability in {"private", "premium_only", "subscriber_only", "needs_auth"}:
            if browser_session is None:
                raise RuntimeError(
                    "İçerik oturum gerektiriyor; yalnız açıkça seçilmiş ayrı profil "
                    "kullanılabilir."
                )
        heights = tuple(
            height
            for height in metadata.get("available_heights") or []
            if isinstance(height, int) and 144 <= height <= MAX_DOWNLOAD_HEIGHT
        )
        if not heights:
            raise RuntimeError(
                f"{index}. video için 1080p sınırı içinde indirilebilir biçim bulunamadı."
            )
        choices: list[FormatChoice] = []
        for height in heights:
            known_bytes = (
                max(1024, math.ceil(float(duration_value) * 0.192 * 1_000_000 / 8))
                if summary_only
                else estimate_download_bytes(media_payload, height)
            )
            budget = _budget_for(
                duration=float(duration_value),
                free_bytes=free_bytes,
                height=height,
                known_source_bytes=known_bytes,
                missing_model_bytes=missing_models,
                processing_requested=processing_requested,
            )
            choices.append(
                FormatChoice(
                    height=height,
                    estimated_download_bytes=budget.estimated_source_bytes,
                    estimated_peak_bytes=budget.estimated_peak_work_bytes,
                    required_bytes=budget.required_bytes,
                    fits=budget.fits,
                )
            )
        fitting = [item.height for item in choices if item.fits]
        if not fitting:
            raise RuntimeError(
                f"{index}. video için hiçbir kalite 20 GiB disk güvenlik payını koruyamıyor."
            )
        media_metadata.append(metadata)
        built_media.append(
            MediaChoice(
                index=index,
                media_id=str(metadata.get("id") or index),
                title=str(metadata.get("title") or f"Video {index}")[:300],
                uploader=(
                    str(metadata["uploader"])[:200] if metadata.get("uploader") else None
                ),
                duration_seconds=float(duration_value),
                manual_subtitles=tuple(metadata.get("subtitle_languages") or ()),
                automatic_subtitles=tuple(
                    metadata.get("automatic_caption_languages") or ()
                ),
                detected_language=(
                    str(metadata["language"]).casefold().split("-", 1)[0]
                    if metadata.get("language")
                    else None
                ),
                available_heights=heights,
                default_height=max(fitting),
                format_choices=tuple(choices),
            )
        )

    first = built_media[0]
    first_metadata = media_metadata[0]
    warnings: list[str] = []
    if attempts > 1:
        warnings.append(
            "İlk metadata denemesi geçici olarak başarısız oldu; bir kez yenilendi."
        )
    if len(built_media) > 1:
        warnings.append("Gönderide birden fazla video var; seçim yapılmadan indirme başlamaz.")
    if browser_session is not None:
        warnings.append(
            "Bu incelemede yalnız seçtiğiniz ayrı tarayıcı profili kullanıldı; "
            "oturum bilgisi KSI Local Studio tarafından kaydedilmedi."
        )
    if validated.platform is Platform.UDEMY:
        warnings.extend(
            (
                "Deneysel Udemy desteği yalnız erişim hakkınızı onayladığınız bu tek "
                "dersi işler.",
                "DRM veya CAPTCHA görülürse işlem aşılmaya çalışılmadan durur; yerel "
                "MP4/SRT/VTT/TXT dosyası kullanabilirsiniz.",
            )
        )
    if first.default_height < max(first.available_heights):
        warnings.append(f"Disk güvenlik payı için {first.default_height}p önerildi.")
    if summary_only:
        available = (*first.manual_subtitles, *first.automatic_subtitles)
        if available:
            warnings.append(
                "Yalnız özet seçildi: uygun kaynak altyazısı indirilip video atlanacak."
            )
        else:
            warnings.append(
                "Yalnız özet seçildi: altyazı yok, tam video yerine yalnız ses indirilecek."
            )
    return PreflightResult(
        source_kind=validated.platform.value,
        platform=(
            "YouTube"
            if validated.platform is Platform.YOUTUBE
            else "X"
            if validated.platform is Platform.X
            else "Udemy (Deneysel)"
        ),
        title=first.title,
        uploader=first.uploader,
        duration_seconds=first.duration_seconds,
        live_status=str(first_metadata.get("live_status") or "") or None,
        entry_count=len(built_media),
        manual_subtitles=first.manual_subtitles,
        automatic_subtitles=first.automatic_subtitles,
        detected_language=first.detected_language,
        requires_authentication=validated.platform is Platform.UDEMY,
        available_heights=first.available_heights,
        default_height=first.default_height,
        format_choices=first.format_choices,
        fixed_budget=None,
        requested_outputs=outputs,
        free_bytes=free_bytes,
        retry_count=attempts - 1,
        warnings=tuple(warnings),
        tool_versions=tuple(f"{item.name} {item.expected_version}" for item in tools.tools),
        media_choices=tuple(built_media) if validated.platform is Platform.X else (),
        session_used=browser_session is not None,
    )
