"""Safe yt-dlp planning for supported sources."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import SplitResult, urlsplit, urlunsplit


class Platform(StrEnum):
    YOUTUBE = "youtube"
    X = "x"
    UDEMY = "udemy"


class DownloadMode(StrEnum):
    VIDEO = "video"
    AUDIO = "audio"
    SUBTITLES = "subtitles"


class SourceURLValidationError(ValueError):
    """Raised when a URL is outside KSI Local Studio's intentionally narrow input policy."""


class FailureCategory(StrEnum):
    TRANSIENT = "transient"
    RATE_LIMIT = "rate_limit"
    FORBIDDEN = "forbidden"
    JAVASCRIPT = "javascript"
    AUTH_REQUIRED = "auth_required"
    PO_TOKEN = "po_token"
    DRM = "drm"
    CAPTCHA = "captcha"
    COURSE_ID = "course_id"
    UNAVAILABLE = "unavailable"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


class DownloaderError(RuntimeError):
    """A sanitized yt-dlp failure with a bounded recovery classification."""

    def __init__(self, message: str, category: FailureCategory) -> None:
        super().__init__(message)
        self.category = category

    @property
    def retryable(self) -> bool:
        return self.category in {FailureCategory.TRANSIENT, FailureCategory.JAVASCRIPT}


_ALLOWED_DOMAINS: dict[Platform, tuple[str, ...]] = {
    Platform.YOUTUBE: ("youtube.com", "youtu.be", "youtube-nocookie.com"),
    Platform.X: ("x.com", "twitter.com"),
    Platform.UDEMY: ("udemy.com",),
}

SUPPORTED_SUBTITLE_LANGUAGES = ("en", "ru", "es", "de", "zh", "fr", "it", "tr")
MAX_DOWNLOAD_HEIGHT = 1080
_RESULT_SUFFIXES = {
    ".aac", ".ass", ".flac", ".m4a", ".mkv", ".mov", ".mp3", ".mp4",
    ".ogg", ".opus", ".srt", ".vtt", ".wav", ".webm",
}


@dataclass(frozen=True)
class ValidatedSource:
    platform: Platform
    url: str
    hostname: str


@dataclass(frozen=True)
class BrowserSession:
    """An explicitly selected browser profile, kept only in process memory."""

    browser: str
    profile_directory: Path

    @property
    def yt_dlp_value(self) -> str:
        return f"{self.browser}:{self.profile_directory}"


@dataclass(frozen=True)
class ProbePlan:
    source: ValidatedSource
    argv: tuple[str, ...]
    session_used: bool = False

    def display_dict(self) -> dict[str, Any]:
        """Return a display-safe representation without echoing query parameters."""
        return {
            "platform": self.source.platform.value,
            "host": self.source.hostname,
            "argv_without_url": _display_safe_argv(self.argv[:-2]),
            "url_argument": "<validated-url>",
            "session_used": self.session_used,
        }


@dataclass(frozen=True)
class DownloadPlan:
    source: ValidatedSource
    output_directory: Path
    argv: tuple[str, ...]
    session_used: bool = False
    direct_https_only: bool = False
    mode: DownloadMode = DownloadMode.VIDEO

    def display_dict(self) -> dict[str, Any]:
        return {
            "platform": self.source.platform.value,
            "host": self.source.hostname,
            "output_directory": str(self.output_directory),
            "argv_without_url": _display_safe_argv(self.argv[:-2]),
            "url_argument": "<validated-url>",
            "session_used": self.session_used,
            "direct_https_only": self.direct_https_only,
            "mode": self.mode.value,
        }


@dataclass(frozen=True)
class DownloadRunResult:
    files: tuple[str, ...]
    attempts: int
    fallback: str | None = None


def _display_safe_argv(argv: tuple[str, ...] | list[str]) -> list[str]:
    """Hide browser profile paths while retaining a useful diagnostic plan."""
    safe = list(argv)
    for index, value in enumerate(safe[:-1]):
        if value == "--cookies-from-browser":
            safe[index + 1] = "<ayrı-tarayıcı-oturumu>"
    return safe


def validate_browser_session(
    browser: str, profile_directory: str | Path
) -> BrowserSession:
    """Validate an opt-in dedicated profile without discovering a main profile."""
    normalized_browser = browser.strip().casefold()
    if normalized_browser not in {"chrome", "firefox"}:
        raise ValueError("Ayrı oturum için yalnız Chrome veya Firefox destekleniyor.")
    profile = Path(profile_directory).expanduser().resolve()
    if not profile.is_dir():
        raise ValueError("Seçilen ayrı tarayıcı profili klasörü bulunamadı.")
    main_profiles = {
        (Path.home() / "Library/Application Support/Google/Chrome").resolve(),
        (Path.home() / "Library/Application Support/Google/Chrome/Default").resolve(),
        (Path.home() / "Library/Application Support/Firefox").resolve(),
    }
    if profile in main_profiles:
        raise ValueError(
            "Ana tarayıcı profili kullanılamaz. X/Udemy için oluşturduğunuz ayrı profil "
            "klasörünü seçin."
        )
    return BrowserSession(normalized_browser, profile)


def _append_browser_session(argv: list[str], session: BrowserSession | None) -> None:
    if session is not None:
        verified = validate_browser_session(session.browser, session.profile_directory)
        argv.extend(("--cookies-from-browser", verified.yt_dlp_value))


def _append_x_network_policy(argv: list[str]) -> None:
    # Keep rate-limit pressure bounded. A 429 is not retried again by our outer
    # process layer after yt-dlp has completed this delayed retry schedule.
    argv.extend(
        (
            "--extractor-args",
            "twitter:api=graphql",
            "--retries",
            "3",
            "--fragment-retries",
            "3",
            "--retry-sleep",
            "http:exp=5:60",
            "--retry-sleep",
            "extractor:exp=5:60",
            "--retry-sleep",
            "fragment:exp=1:20",
        )
    )


def _append_udemy_network_policy(argv: list[str]) -> None:
    """Keep experimental Udemy access deliberately slow and bounded."""
    argv.extend(
        (
            "--extractor-retries",
            "1",
            "--sleep-requests",
            "1",
            "--sleep-interval",
            "1",
            "--max-sleep-interval",
            "3",
        )
    )


def _host_matches(hostname: str, domain: str) -> bool:
    return hostname == domain or hostname.endswith(f".{domain}")


def _platform_for_host(hostname: str) -> Platform:
    for platform, domains in _ALLOWED_DOMAINS.items():
        if any(_host_matches(hostname, domain) for domain in domains):
            return platform
    raise SourceURLValidationError("Yalnız YouTube, X ve Udemy alan adları destekleniyor.")


def validate_source_url(raw_url: str) -> ValidatedSource:
    """Validate and normalize a user URL without performing network access."""
    candidate = raw_url.strip()
    if not candidate:
        raise SourceURLValidationError("URL boş olamaz.")

    parsed = urlsplit(candidate)
    if parsed.scheme.lower() != "https":
        raise SourceURLValidationError("Yalnız HTTPS bağlantıları kabul ediliyor.")
    if parsed.username or parsed.password:
        raise SourceURLValidationError("URL içinde kullanıcı adı veya parola bulunamaz.")
    if not parsed.hostname:
        raise SourceURLValidationError("Geçerli bir alan adı bulunamadı.")
    if parsed.port not in (None, 443):
        raise SourceURLValidationError("Yalnız varsayılan HTTPS portu kabul ediliyor.")

    hostname = parsed.hostname.rstrip(".").lower()
    platform = _platform_for_host(hostname)
    path = parsed.path or "/"
    query = parsed.query
    if platform is Platform.UDEMY:
        # The broad course extractor can enumerate a whole course. KSI Local Studio accepts
        # exactly one numeric lecture and rewrites modern URLs to the narrow legacy
        # lecture form currently recognized by yt-dlp's UdemyIE.
        lecture_match = re.fullmatch(
            r"/(?:course/)?(?P<slug>[A-Za-z0-9_-]+)/learn/"
            r"(?:(?:v4/t/)?lecture/)(?P<lecture_id>\d+)/?",
            path,
        )
        if lecture_match is None:
            raise SourceURLValidationError(
                "Udemy için kurs sayfası değil, tek bir dersin /learn/lecture/ID "
                "bağlantısını kullanın."
            )
        path = (
            f"/{lecture_match.group('slug')}/learn/v4/t/lecture/"
            f"{lecture_match.group('lecture_id')}"
        )
        # Tracking and course-player state parameters are unnecessary and may be
        # sensitive, so the canonical single-lecture URL never retains them.
        query = ""
    normalized = SplitResult(
        scheme="https",
        netloc=parsed.netloc.lower(),
        path=path,
        query=query,
        fragment="",
    )
    return ValidatedSource(platform, urlunsplit(normalized), hostname)


def build_probe_plan(
    raw_url: str,
    *,
    yt_dlp_path: str = "yt-dlp",
    js_runtime: tuple[str, str] | None = None,
    browser_session: BrowserSession | None = None,
    udemy_access_confirmed: bool = False,
) -> ProbePlan:
    """Build a no-download metadata probe command as an argument array."""
    source = validate_source_url(raw_url)
    if browser_session is not None and source.platform not in {Platform.X, Platform.UDEMY}:
        raise ValueError(
            "Ayrı tarayıcı oturumu yalnız X veya Udemy bağlantılarında kullanılabilir."
        )
    if source.platform is Platform.UDEMY:
        if not udemy_access_confirmed:
            raise ValueError("Udemy dersine erişim hakkı açıkça onaylanmalıdır.")
        if browser_session is None:
            raise ValueError(
                "Udemy için giriş yapılmış ayrı tarayıcı profili seçilmelidir."
            )
    argv: list[str] = [
        yt_dlp_path,
        "--ignore-config",
        "--no-plugin-dirs",
        "--no-remote-components",
        "--skip-download",
        "--dump-single-json",
        "--no-cache-dir",
        "--socket-timeout",
        "20",
    ]

    # A single YouTube/Udemy item is the MVP. X can expose multiple media items in
    # one post, so its metadata probe is capped and returned for explicit selection.
    if source.platform is Platform.X:
        argv.extend(("--yes-playlist", "--playlist-end", "10"))
        _append_x_network_policy(argv)
    elif source.platform is Platform.UDEMY:
        argv.append("--no-playlist")
        _append_udemy_network_policy(argv)
    else:
        argv.append("--no-playlist")

    if js_runtime is not None:
        runtime_name, runtime_path = js_runtime
        if runtime_name not in {"deno", "node"}:
            raise ValueError("Desteklenen JS çalışma ortamları deno ve node'dur.")
        resolved_runtime = str(Path(runtime_path).expanduser().resolve())
        argv.extend(("--js-runtimes", f"{runtime_name}:{resolved_runtime}"))

    _append_browser_session(argv, browser_session)

    # `--` prevents a URL beginning with dashes from becoming an option.
    argv.extend(("--", source.url))
    return ProbePlan(source, tuple(argv), browser_session is not None)


def build_download_plan(
    raw_url: str,
    *,
    output_directory: str | Path,
    yt_dlp_path: str,
    ffmpeg_path: str | None = None,
    js_runtime: tuple[str, str] | None = None,
    max_height: int = MAX_DOWNLOAD_HEIGHT,
    subtitle_languages: tuple[str, ...] = SUPPORTED_SUBTITLE_LANGUAGES,
    media_index: int = 1,
    browser_session: BrowserSession | None = None,
    direct_https_only: bool = False,
    mode: DownloadMode | str = DownloadMode.VIDEO,
    udemy_access_confirmed: bool = False,
) -> DownloadPlan:
    """Build a bounded download command without invoking a shell.

    Browser authentication is absent unless an already validated, explicitly
    selected dedicated profile is supplied. No cookie file is created.
    """
    source = validate_source_url(raw_url)
    try:
        selected_mode = DownloadMode(mode)
    except ValueError as error:
        raise ValueError("İndirme modu video, audio veya subtitles olmalıdır.") from error
    if browser_session is not None and source.platform not in {Platform.X, Platform.UDEMY}:
        raise ValueError(
            "Ayrı tarayıcı oturumu yalnız X veya Udemy bağlantılarında kullanılabilir."
        )
    if source.platform is Platform.UDEMY:
        if not udemy_access_confirmed:
            raise ValueError("Udemy dersine erişim hakkı açıkça onaylanmalıdır.")
        if browser_session is None:
            raise ValueError(
                "Udemy için giriş yapılmış ayrı tarayıcı profili seçilmelidir."
            )
    if isinstance(max_height, bool) or not isinstance(max_height, int):
        raise ValueError("Görüntü yüksekliği tam sayı olmalıdır.")
    if max_height < 144 or max_height > MAX_DOWNLOAD_HEIGHT:
        raise ValueError("Görüntü yüksekliği 144p ile 1080p arasında olmalıdır.")
    if isinstance(media_index, bool) or not isinstance(media_index, int):
        raise ValueError("Medya sırası tam sayı olmalıdır.")
    if media_index < 1 or media_index > 10:
        raise ValueError("Medya sırası 1 ile 10 arasında olmalıdır.")
    if source.platform is not Platform.X and media_index != 1:
        raise ValueError("Çoklu medya seçimi yalnız X gönderileri için kullanılabilir.")
    if source.platform is not Platform.X and direct_https_only:
        raise ValueError("Doğrudan HTTPS geri dönüşü yalnız X için kullanılabilir.")
    if direct_https_only and selected_mode is not DownloadMode.VIDEO:
        raise ValueError("Doğrudan HTTPS geri dönüşü yalnız video modunda kullanılabilir.")
    invalid_languages = set(subtitle_languages) - set(SUPPORTED_SUBTITLE_LANGUAGES)
    if invalid_languages:
        raise ValueError("Desteklenmeyen altyazı dili istendi.")
    if selected_mode is DownloadMode.SUBTITLES and not subtitle_languages:
        raise ValueError("Yalnız altyazı indirmek için bir kaynak dili gereklidir.")
    directory = Path(output_directory).expanduser().resolve()
    if not directory.exists() or not directory.is_dir():
        raise ValueError(
            "İndirme klasörü önceden oluşturulmuş normal bir klasör olmalıdır."
        )

    argv: list[str] = [
        str(Path(yt_dlp_path).expanduser().resolve()),
        "--ignore-config",
        "--no-plugin-dirs",
        "--no-remote-components",
        "--no-cache-dir",
        "--newline",
        "--socket-timeout",
        "30",
        "--retries",
        (
            "3"
            if source.platform is Platform.X
            else "2"
            if source.platform is Platform.UDEMY
            else "10"
        ),
        "--fragment-retries",
        "3" if source.platform in {Platform.X, Platform.UDEMY} else "10",
        "--continue",
    ]
    output_template = directory / (
        "source-direct.%(ext)s" if direct_https_only else "source.%(ext)s"
    )
    if selected_mode is DownloadMode.VIDEO:
        argv.extend(
            (
                "--format",
                (
                    f"b[height<={max_height}][protocol=https][ext=mp4]/"
                    f"b[height<={max_height}][protocol^=http][ext=mp4]"
                    if direct_https_only
                    else (
                        f"bv*[height<={max_height}][vcodec^=avc1]+ba[acodec^=mp4a]/"
                        f"bv*[height<={max_height}]+ba/b[height<={max_height}]/b"
                    )
                ),
                "--merge-output-format",
                "mp4",
                "--output",
                str(output_template),
            )
        )
    elif selected_mode is DownloadMode.AUDIO:
        argv.extend(
            (
                "--format",
                "ba[acodec^=mp4a]/ba",
                "--extract-audio",
                "--audio-format",
                "m4a",
                "--output",
                str(output_template),
            )
        )
    else:
        argv.extend(("--skip-download", "--output", str(output_template)))
    if source.platform is Platform.X:
        argv.extend(("--yes-playlist", "--playlist-items", str(media_index)))
        _append_x_network_policy(argv)
    elif source.platform is Platform.UDEMY:
        argv.append("--no-playlist")
        _append_udemy_network_policy(argv)
    else:
        argv.append("--no-playlist")
    if subtitle_languages:
        patterns = ",".join(f"{code}.*" for code in subtitle_languages)
        argv.extend(
            (
                "--write-subs",
                "--write-auto-subs",
                "--sub-langs",
                patterns,
                "--sub-format",
                "srt/best",
                "--convert-subs",
                "srt",
            )
        )
    if ffmpeg_path:
        argv.extend(("--ffmpeg-location", str(Path(ffmpeg_path).expanduser().resolve())))
    if js_runtime is not None:
        runtime_name, runtime_path = js_runtime
        if runtime_name not in {"deno", "node"}:
            raise ValueError("Desteklenen JS çalışma ortamları deno ve node'dur.")
        argv.extend(
            (
                "--js-runtimes",
                f"{runtime_name}:{Path(runtime_path).expanduser().resolve()}",
            )
        )
    _append_browser_session(argv, browser_session)
    argv.extend(("--", source.url))
    return DownloadPlan(
        source,
        directory,
        tuple(argv),
        browser_session is not None,
        direct_https_only,
        selected_mode,
    )


def _safe_failure(stderr: str, source_url: str, fallback: str) -> str:
    last_line = next(
        (line.strip() for line in reversed(stderr.splitlines()) if line.strip()),
        fallback,
    )
    safe_line = last_line.replace(source_url, "<redacted-url>")
    safe_line = re.sub(r"https?://\S+", "<redacted-url>", safe_line)
    safe_line = re.sub(
        r"(?i)(?:chrome|firefox):/[^\r\n]+", "<ayrı-tarayıcı-oturumu>", safe_line
    )
    safe_line = re.sub(
        r"(?i)\b(auth_token|ct0|guest_token|x-csrf-token)\b\s*[:=]\s*[^\s,;]+",
        lambda match: f"{match.group(1)}=[gizlendi]",
        safe_line,
    )
    return safe_line[:500]


def classify_failure(stderr: str) -> FailureCategory:
    lowered = stderr.casefold()
    if any(
        term in lowered
        for term in (
            "captcha",
            "verify you are human",
            "verify that you are human",
            "anti-automation",
        )
    ):
        return FailureCategory.CAPTCHA
    if any(
        term in lowered
        for term in (
            "unable to extract course id",
            "could not extract course id",
            "[udemy:course]",
        )
    ):
        return FailureCategory.COURSE_ID
    if any(term in lowered for term in ("http error 429", "too many requests", "rate limit")):
        return FailureCategory.RATE_LIMIT
    if any(term in lowered for term in ("http error 403", "forbidden")):
        return FailureCategory.FORBIDDEN
    if any(term in lowered for term in ("drm", "widevine", "fairplay", "protected by")):
        return FailureCategory.DRM
    if any(term in lowered for term in ("po token", "po-token", "pot provider")):
        return FailureCategory.PO_TOKEN
    if any(
        term in lowered
        for term in (
            "sign in",
            "login required",
            "members-only",
            "private video",
            "confirm your age",
            "cookies-from-browser",
        )
    ):
        return FailureCategory.AUTH_REQUIRED
    if any(term in lowered for term in ("not available", "video unavailable", "removed")):
        return FailureCategory.UNAVAILABLE
    if any(
        term in lowered
        for term in ("javascript runtime", "no supported javascript", "ejs", "challenge")
    ):
        return FailureCategory.JAVASCRIPT
    if any(
        term in lowered
        for term in (
            "timed out",
            "timeout",
            "temporary failure",
            "connection reset",
            "http error 5",
        )
    ):
        return FailureCategory.TRANSIENT
    if "unsupported url" in lowered:
        return FailureCategory.UNSUPPORTED
    return FailureCategory.UNKNOWN


def _raise_download_error(stderr: str, source_url: str, fallback: str) -> None:
    category = classify_failure(stderr)
    safe_line = _safe_failure(stderr, source_url, fallback)
    guidance = {
        FailureCategory.AUTH_REQUIRED: (
            " İçerik oturum istiyor; hesap oturumu bu fazda otomatik kullanılmadı."
        ),
        FailureCategory.PO_TOKEN: (
            " Açık PO-token hatası görüldü; doğrulanmamış bir sağlayıcı "
            "otomatik başlatılmadı."
        ),
        FailureCategory.DRM: " DRM korumalı içerikte işlem güvenli biçimde durduruldu.",
        FailureCategory.CAPTCHA: (
            " CAPTCHA/insan doğrulaması aşılmaya çalışılmadan işlem güvenli biçimde "
            "durduruldu."
        ),
        FailureCategory.COURSE_ID: (
            " Udemy ders çözücüsü bu bağlantıyı güvenle tanıyamadı; kursun tamamı "
            "denenmedi."
        ),
        FailureCategory.JAVASCRIPT: " YouTube JavaScript çözümleme zinciri başarısız oldu.",
        FailureCategory.RATE_LIMIT: (
            " X istek sınırına ulaşıldı. Bekleyip daha sonra yeniden deneyin; "
            "uygulama art arda yeni istek göndermedi."
        ),
        FailureCategory.FORBIDDEN: (
            " Medya sunucusu isteği reddetti; yalnız X için tek doğrudan HTTPS "
            "geri dönüşü kullanılabilir."
        ),
    }.get(category, "")
    try:
        is_udemy = validate_source_url(source_url).platform is Platform.UDEMY
    except SourceURLValidationError:
        is_udemy = False
    if is_udemy:
        guidance += (
            " DRM veya CAPTCHA atlatılmaz. Yetkili yerel MP4/SRT/VTT/TXT dosyasını "
            "uygulamaya sürükleyebilirsiniz."
        )
    raise DownloaderError(f"{safe_line}{guidance}", category)


def run_download(plan: DownloadPlan, *, timeout_seconds: int = 4 * 60 * 60) -> list[str]:
    """Run a validated download and return the resulting ordinary files."""
    try:
        completed = subprocess.run(
            list(plan.argv),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            shell=False,
        )
    except subprocess.TimeoutExpired as error:
        raise DownloaderError(
            "Video indirme işlemi zaman aşımına uğradı.", FailureCategory.TRANSIENT
        ) from error
    except OSError as error:
        raise DownloaderError(
            "Video indirme aracı çalıştırılamadı.", FailureCategory.UNKNOWN
        ) from error
    if completed.returncode != 0:
        _raise_download_error(
            completed.stderr, plan.source.url, "Video indirme başarısız oldu."
        )
    return sorted(
        str(item)
        for item in plan.output_directory.iterdir()
        if (
            item.is_file()
            and not item.is_symlink()
            and not item.name.startswith("._")
            and item.suffix.casefold() in _RESULT_SUFFIXES
        )
    )


def run_download_with_retry(
    plan: DownloadPlan,
    *,
    timeout_seconds: int = 4 * 60 * 60,
    direct_fallback_plan: DownloadPlan | None = None,
) -> DownloadRunResult:
    """Resume once, or make one explicit X direct-HTTPS fallback after a 403."""
    try:
        return DownloadRunResult(tuple(run_download(plan, timeout_seconds=timeout_seconds)), 1)
    except DownloaderError as error:
        if error.category is FailureCategory.FORBIDDEN and direct_fallback_plan is not None:
            files = run_download(direct_fallback_plan, timeout_seconds=timeout_seconds)
            return DownloadRunResult(tuple(files), 2, "direct_https_after_403")
        if not error.retryable:
            raise
    files = run_download(plan, timeout_seconds=timeout_seconds)
    return DownloadRunResult(tuple(files), 2, "same_plan_retry")


def run_probe(plan: ProbePlan, *, timeout_seconds: int = 60) -> dict[str, Any]:
    """Execute a previously validated metadata-only plan without a shell."""
    try:
        completed = subprocess.run(
            list(plan.argv),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            shell=False,
        )
    except subprocess.TimeoutExpired as error:
        raise DownloaderError(
            "Bağlantı ön incelemesi zaman aşımına uğradı.", FailureCategory.TRANSIENT
        ) from error
    except OSError as error:
        raise DownloaderError(
            "Bağlantı ön inceleme aracı çalıştırılamadı.", FailureCategory.UNKNOWN
        ) from error
    if completed.returncode != 0:
        # Do not expose the command or original URL. Authentication-bearing query
        # parameters must never enter UI logs through this layer.
        _raise_download_error(
            completed.stderr, plan.source.url, "yt-dlp ön incelemesi başarısız oldu."
        )

    payload = json.loads(completed.stdout)
    if not isinstance(payload, dict):
        raise RuntimeError("yt-dlp beklenen JSON nesnesini döndürmedi.")
    return payload


def run_probe_with_retry(
    plan: ProbePlan, *, timeout_seconds: int = 60
) -> tuple[dict[str, Any], int]:
    """Try the tested tool once more only for transient/JavaScript failures."""
    try:
        return run_probe(plan, timeout_seconds=timeout_seconds), 1
    except DownloaderError as error:
        if not error.retryable:
            raise
    return run_probe(plan, timeout_seconds=timeout_seconds), 2


def _estimated_format_bytes(item: dict[str, Any], duration_seconds: float | None) -> int | None:
    for key in ("filesize", "filesize_approx"):
        value = item.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return int(value)
    bitrate = item.get("tbr")
    if (
        isinstance(bitrate, (int, float))
        and bitrate > 0
        and isinstance(duration_seconds, (int, float))
        and duration_seconds > 0
    ):
        return int(float(bitrate) * 1000 / 8 * float(duration_seconds))
    return None


def estimate_download_bytes(payload: dict[str, Any], max_height: int) -> int | None:
    """Estimate the selected best video and audio formats from yt-dlp metadata."""
    formats = [item for item in payload.get("formats") or [] if isinstance(item, dict)]
    duration = payload.get("duration")
    duration_value = float(duration) if isinstance(duration, (int, float)) else None
    video_candidates = [
        item
        for item in formats
        if item.get("vcodec") not in (None, "none")
        and isinstance(item.get("height"), (int, float))
        and 0 < float(item["height"]) <= max_height
    ]
    if not video_candidates:
        return None
    compatible_video = [
        item for item in video_candidates if str(item.get("vcodec") or "").startswith("avc1")
    ]
    if compatible_video:
        video_candidates = compatible_video
    selected_video = max(
        video_candidates,
        key=lambda item: (
            float(item.get("height") or 0),
            float(item.get("fps") or 0),
            float(item.get("tbr") or 0),
        ),
    )
    video_bytes = _estimated_format_bytes(selected_video, duration_value)
    if video_bytes is None:
        return None
    if selected_video.get("acodec") not in (None, "none"):
        return video_bytes
    audio_candidates = [
        item
        for item in formats
        if item.get("acodec") not in (None, "none")
        and item.get("vcodec") in (None, "none")
    ]
    if not audio_candidates:
        return video_bytes
    compatible_audio = [
        item for item in audio_candidates if str(item.get("acodec") or "").startswith("mp4a")
    ]
    if compatible_audio:
        audio_candidates = compatible_audio
    selected_audio = max(audio_candidates, key=lambda item: float(item.get("abr") or 0))
    audio_bytes = _estimated_format_bytes(selected_audio, duration_value)
    return video_bytes + audio_bytes if audio_bytes is not None else video_bytes


def summarize_probe(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep only normalized, non-authentication metadata needed by the UI."""
    entries = payload.get("entries")
    normalized_entries = [entry for entry in entries or [] if isinstance(entry, dict)]
    formats = payload.get("formats")
    normalized_formats = [item for item in formats or [] if isinstance(item, dict)]

    heights = sorted(
        {
            int(item["height"])
            for item in normalized_formats
            if isinstance(item.get("height"), (int, float)) and item["height"] > 0
        }
    )
    subtitles = payload.get("subtitles")
    automatic_captions = payload.get("automatic_captions")
    subtitle_languages = sorted(subtitles) if isinstance(subtitles, dict) else []
    automatic_languages = (
        sorted(automatic_captions) if isinstance(automatic_captions, dict) else []
    )
    def supported(language: str) -> bool:
        base = language.casefold().split("-", 1)[0]
        return base in SUPPORTED_SUBTITLE_LANGUAGES

    return {
        "extractor": payload.get("extractor_key") or payload.get("extractor"),
        "id": payload.get("id"),
        "title": payload.get("title"),
        "uploader": payload.get("uploader") or payload.get("channel"),
        "duration_seconds": payload.get("duration"),
        "live_status": payload.get("live_status"),
        "availability": payload.get("availability"),
        "language": payload.get("language"),
        "entry_count": len(normalized_entries) if entries is not None else 1,
        "subtitle_language_count": len(subtitle_languages),
        "subtitle_languages": [lang for lang in subtitle_languages if supported(lang)],
        "automatic_caption_language_count": len(automatic_languages),
        "automatic_caption_languages": [
            lang for lang in automatic_languages if supported(lang)
        ],
        "available_heights": heights,
        "format_count": len(normalized_formats),
    }
