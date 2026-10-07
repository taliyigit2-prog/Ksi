"""Phase 11 dependency, model, cache and package acceptance inventory."""

from __future__ import annotations

import platform
import plistlib
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ksi_local import __version__
from ksi_local.i18n import ui_text
from ksi_local.media import sha256_file
from ksi_local.project_metadata import APP_BUNDLE_NAME, APP_EXECUTABLE_NAME
from ksi_local.settings import WorkspacePaths
from ksi_local.storage import MIN_FREE_RESERVE_BYTES
from ksi_local.system_health import HealthReport, build_health_report
from ksi_local.tool_integrity import load_tool_manifest, verify_download_tools
from ksi_local.bundle_runtime import bundle_root, host_architecture


@dataclass(frozen=True)
class ModelStatus:
    key: str
    label: str
    purpose: str
    expected_bytes: int
    actual_bytes: int
    ready: bool
    digest_verified: bool | None
    note: str


@dataclass(frozen=True)
class CacheStatus:
    job_count: int
    candidate_file_count: int
    candidate_bytes: int
    interrupted_quarantine_count: int


@dataclass(frozen=True)
class AppBundleStatus:
    path: str
    exists: bool
    signed: bool
    version: str | None
    bundle_version: str | None
    identifier: str | None
    native_arm64_only: bool
    note: str
    native_architecture: str | None = None
    native_architecture_matches_host: bool = False


@dataclass(frozen=True)
class AcceptanceReport:
    app_version: str
    architecture: str
    health: HealthReport
    workspace: str
    workspace_total_bytes: int
    workspace_free_bytes: int
    tool_integrity_ok: bool
    tool_integrity_note: str
    models: tuple[ModelStatus, ...]
    cache: CacheStatus
    app_bundle: AppBundleStatus
    passed: bool
    full_model_verification: bool

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["health"] = asdict(self.health)
        return payload


def _ordinary_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink() and not path.name.startswith("._")


def _artifact_status(
    *,
    key: str,
    label: str,
    purpose: str,
    artifacts: tuple[tuple[Path, int, str], ...],
    verify_hashes: bool,
) -> ModelStatus:
    actual_bytes = 0
    ready = True
    digest_verified: bool | None = True if verify_hashes else None
    problem = ""
    for path, expected_size, expected_digest in artifacts:
        if not _ordinary_file(path):
            ready = False
            digest_verified = False if verify_hashes else None
            problem = f"Eksik dosya: {path.name}"
            continue
        actual_size = path.stat().st_size
        actual_bytes += actual_size
        if actual_size != expected_size:
            ready = False
            digest_verified = False if verify_hashes else None
            problem = f"Boyut uyuşmuyor: {path.name}"
            continue
        if verify_hashes and sha256_file(path) != expected_digest.casefold():
            ready = False
            digest_verified = False
            problem = f"SHA-256 uyuşmuyor: {path.name}"
    expected_bytes = sum(item[1] for item in artifacts)
    if ready and verify_hashes:
        problem = "Boyut ve SHA-256 doğrulandı."
    elif ready:
        problem = "Dosyalar ve boyutlar hazır; tam SHA-256 isteğe bağlıdır."
    return ModelStatus(
        key=key,
        label=label,
        purpose=purpose,
        expected_bytes=expected_bytes,
        actual_bytes=actual_bytes,
        ready=ready,
        digest_verified=digest_verified,
        note=problem,
    )


def inspect_models(
    workspace: WorkspacePaths,
    *,
    verify_hashes: bool = False,
    manifest: dict[str, Any] | None = None,
) -> tuple[ModelStatus, ...]:
    """Use the sealed architecture catalog; preserve legacy development checks."""
    resources = bundle_root()
    if resources is not None and manifest is None:
        from ksi_local.model_manager import ModelManager
        inventory = ModelManager(resources, workspace.root / "models").inventory(verify=verify_hashes)
        return tuple(ModelStatus(item.identifier, item.title, item.description,
            item.total_bytes, item.installed_bytes, item.state in {"installed", "verified"},
            (item.state == "verified") if verify_hashes else None,
            "" if item.state in {"installed", "verified"} else item.state) for item in inventory)
    payload = manifest or load_tool_manifest()
    models = payload.get("models")
    if not isinstance(models, dict):
        raise RuntimeError("Araç manifestinde model kayıtları bulunamadı.")

    qwen = models["qwen3.5:4b"]
    translate = models["translategemma:4b-it-q8_0"]
    whisper = models["mlx-community/whisper-large-v3-turbo-8bit"]
    chatterbox = models["ResembleAI/chatterbox-multilingual-v3"]
    ollama_blobs = workspace.models_ollama / "blobs"
    tts = workspace.root / "models/tts/chatterbox-multilingual-v3"
    qwen_artifacts = [(ollama_blobs / f"sha256-{qwen['sha256']}", int(qwen["size_bytes"]), str(qwen["sha256"]))]
    if qwen.get("projector_sha256"):
        qwen_artifacts.append((ollama_blobs / f"sha256-{qwen['projector_sha256']}", int(qwen["projector_size_bytes"]), str(qwen["projector_sha256"])))
    return (
        _artifact_status(
            key="qwen3.5:4b",
            label="Qwen3.5 4B",
            purpose="Türkçe özet",
            artifacts=tuple(qwen_artifacts),
            verify_hashes=verify_hashes,
        ),
        _artifact_status(
            key="translategemma:4b-it-q8_0",
            label="TranslateGemma 4B Q8",
            purpose="Türkçe çeviri",
            artifacts=((
                ollama_blobs / f"sha256-{translate['sha256']}",
                int(translate["size_bytes"]),
                str(translate["sha256"]),
            ),),
            verify_hashes=verify_hashes,
        ),
        _artifact_status(
            key="mlx-community/whisper-large-v3-turbo-8bit",
            label="Whisper large-v3-turbo 8-bit",
            purpose="Konuşma ve dil algılama",
            artifacts=((
                workspace.models_whisper / str(whisper["weights_filename"]),
                int(whisper["size_bytes"]),
                str(whisper["sha256"]),
            ),),
            verify_hashes=verify_hashes,
        ),
        _artifact_status(
            key="ResembleAI/chatterbox-multilingual-v3",
            label="Chatterbox Multilingual v3",
            purpose="Düşük tonlu erkek Türkçe dublaj",
            artifacts=(
                (
                    tts / "s3gen.pt",
                    int(chatterbox["s3gen_size_bytes"]),
                    str(chatterbox["s3gen_sha256"]),
                ),
                (
                    tts / "t3_mtl23ls_v3.safetensors",
                    int(chatterbox["t3_v3_size_bytes"]),
                    str(chatterbox["t3_v3_sha256"]),
                ),
                (
                    tts / "ve.pt",
                    int(chatterbox["voice_encoder_size_bytes"]),
                    str(chatterbox["voice_encoder_sha256"]),
                ),
            ),
            verify_hashes=verify_hashes,
        ),
    )


def inspect_job_cache(workspace: WorkspacePaths) -> CacheStatus:
    """Count regenerable job files without modifying or following links."""
    jobs = workspace.jobs
    if not jobs.is_dir() or jobs.is_symlink():
        return CacheStatus(0, 0, 0, 0)
    candidate_paths: set[Path] = set()
    quarantine_count = 0
    job_count = 0
    for job in jobs.iterdir():
        if not job.is_dir() or job.is_symlink() or job.name.startswith("."):
            continue
        job_count += 1
        quarantine_count += sum(
            1
            for item in job.glob(".cleanup-quarantine-*")
            if item.is_dir() and not item.is_symlink()
        )
        segment_root = job / "work/dub-segments"
        if segment_root.is_dir() and not segment_root.is_symlink():
            candidate_paths.update(
                item.resolve() for item in segment_root.rglob("*") if _ordinary_file(item)
            )
        recognized = job / "work/turkce-dublaj.asr.srt"
        if (job / "outputs/turkce-dublaj.kalite.json").is_file() and _ordinary_file(
            recognized
        ):
            candidate_paths.add(recognized.resolve())
        audio = job / "outputs/turkce-dublaj.wav"
        if (
            (job / "outputs/turkce-dublaj.mp4").is_file()
            and (job / "outputs/turkce-dublaj.kalite.json").is_file()
            and _ordinary_file(audio)
        ):
            candidate_paths.add(audio.resolve())
        for item in job.rglob("*"):
            if _ordinary_file(item) and item.name.endswith(
                (".part", ".partial", ".checkpoint.json")
            ):
                candidate_paths.add(item.resolve())
    safe_paths = [item for item in candidate_paths if item.is_relative_to(jobs.resolve())]
    return CacheStatus(
        job_count=job_count,
        candidate_file_count=len(safe_paths),
        candidate_bytes=sum(item.stat().st_size for item in safe_paths),
        interrupted_quarantine_count=quarantine_count,
    )


def inspect_app_bundle(path: str | Path | None = None) -> AppBundleStatus:
    resources = bundle_root()
    packaged = resources.parent.parent if resources is not None else None
    target = Path(path or packaged or (Path.home() / "Desktop" / APP_BUNDLE_NAME)).expanduser().resolve()
    plist_path = target / "Contents/Info.plist"
    launcher = target / "Contents/MacOS" / APP_EXECUTABLE_NAME
    if not target.is_dir() or not plist_path.is_file() or not launcher.is_file():
        return AppBundleStatus(
            str(target), False, False, None, None, None, False, "Uygulama paketi bulunamadı."
        )
    try:
        with plist_path.open("rb") as handle:
            plist = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException):
        return AppBundleStatus(
            str(target), True, False, None, None, None, False, "Info.plist okunamadı."
        )
    try:
        completed = subprocess.run(
            ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(target)],
            check=False,
            capture_output=True,
            timeout=20,
            shell=False,
        )
        signed = completed.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        signed = False
    priorities = plist.get("LSArchitecturePriority")
    native_only = bool(
        plist.get("LSRequiresNativeExecution") is True
        and isinstance(priorities, list)
        and priorities == ["arm64"]
    )
    declared = plist.get("KSIArchitecture") or (priorities[0] if isinstance(priorities, list) and len(priorities) == 1 else None)
    matches = bool(plist.get("LSRequiresNativeExecution") is True and declared in {"arm64", "x86_64"} and priorities == [declared] and declared == host_architecture())
    note = "İmza ve native işlemci çalışma ayarı doğrulandı." if signed and matches else "Paket imzası veya native işlemci ayarı doğrulanamadı."
    return AppBundleStatus(
        path=str(target),
        exists=True,
        signed=signed,
        version=str(plist.get("CFBundleShortVersionString") or "") or None,
        bundle_version=str(plist.get("CFBundleVersion") or "") or None,
        identifier=str(plist.get("CFBundleIdentifier") or "") or None,
        native_arm64_only=native_only,
        note=note,
        native_architecture=declared,
        native_architecture_matches_host=matches,
    )


def build_acceptance_report(
    workspace: WorkspacePaths,
    *,
    verify_model_hashes: bool = False,
    app_path: str | Path | None = None,
) -> AcceptanceReport:
    health = build_health_report()
    usage = shutil.disk_usage(workspace.root)
    models = inspect_models(workspace, verify_hashes=verify_model_hashes)
    cache = inspect_job_cache(workspace)
    app_bundle = inspect_app_bundle(app_path)
    tool_paths = {tool.name: tool.path for tool in health.tools}
    try:
        verify_download_tools(
            yt_dlp_path=workspace.yt_dlp,
            deno_path=workspace.deno,
            ffmpeg_path=tool_paths.get("ffmpeg"),
            ffprobe_path=tool_paths.get("ffprobe"),
        )
    except (OSError, RuntimeError, ValueError) as error:
        tool_ok = False
        tool_note = str(error)
    else:
        tool_ok = True
        tool_note = "yt-dlp, Deno, FFmpeg ve ffprobe doğrulandı."
    required_tools = {
        "ffmpeg",
        "ffprobe",
        "yt-dlp",
        "deno",
        "ollama",
        "Apple Vision OCR",
    }
    installed = {item.name for item in health.tools if item.installed}
    app_ok = (
        app_bundle.exists
        and app_bundle.signed
        and app_bundle.native_architecture_matches_host
        and app_bundle.version == __version__
    )
    passed = bool(
        platform.machine() == "arm64"
        and usage.free >= MIN_FREE_RESERVE_BYTES
        and required_tools.issubset(installed)
        and health.youtube_js_ready
        and tool_ok
        and all(model.ready for model in models)
        and app_ok
    )
    return AcceptanceReport(
        app_version=__version__,
        architecture=platform.machine(),
        health=health,
        workspace=str(workspace.root),
        workspace_total_bytes=usage.total,
        workspace_free_bytes=usage.free,
        tool_integrity_ok=tool_ok,
        tool_integrity_note=tool_note,
        models=models,
        cache=cache,
        app_bundle=app_bundle,
        passed=passed,
        full_model_verification=verify_model_hashes,
    )


def format_acceptance_report(report: AcceptanceReport, *, language: str = "tr") -> str:
    t = lambda key, **values: ui_text(key, language, **values)
    state = t("report.ready") if report.passed else t("report.check")
    lines = [
        f"KSI Local Studio {report.app_version} — {state}",
        f"{t('report.architecture')}: {report.architecture}",
        f"{t('report.storage')}: {report.workspace}",
        f"{t('report.free')}: {report.workspace_free_bytes / 1024**3:.1f} GiB",
        f"{t('report.tools')}: {t('report.verified') if report.tool_integrity_ok else t('report.problem')}",
        f"{t('report.package')}: {t('report.package_ready') if report.app_bundle.signed and report.app_bundle.native_architecture_matches_host else t('report.package_check')}",
        "",
        t("report.models"),
    ]
    purpose_keys = {
        "qwen3.5:4b": "report.purpose_summary",
        "translategemma:4b-it-q8_0": "report.purpose_translation",
        "mlx-community/whisper-large-v3-turbo-8bit": "report.purpose_speech",
        "ResembleAI/chatterbox-multilingual-v3": "report.purpose_dubbing",
    }
    for model in report.models:
        verified = t("report.hash_ok") if model.digest_verified is True else ""
        purpose = t(purpose_keys[model.key]) if model.key in purpose_keys else model.purpose
        lines.append(
            f"- {model.label}: {t('report.model_ready') if model.ready else t('report.model_problem')} · "
            f"{model.actual_bytes / 1024**3:.2f} GiB{verified} · {purpose}"
        )
    lines.extend(
        (
            "",
            t("report.cache"),
            t("report.cache_line", count=report.cache.candidate_file_count, size=report.cache.candidate_bytes / 1024**2),
            t("report.cleanup_note"),
            "",
            t("report.ollama_note"),
        )
    )
    return "\n".join(lines)
