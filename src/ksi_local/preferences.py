"""Small private UI preference store; no media, URLs, or credentials are stored."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from ksi_local.atomic_files import atomic_write_json
from ksi_local.job_store import default_database_path
from ksi_local.i18n import SUPPORTED_UI_LANGUAGES


EXPORT_KINDS = ("all", "video", "summary", "subtitle", "translation")
THEMES = ("system", "light", "dark")
TRANSLATION_ENGINES = ("gemma", "argos")
ONBOARDING_VERSION = 2


@dataclass(frozen=True)
class UserPreferences:
    export_kind: str = "all"
    icloud_warning_acknowledged: bool = False
    onboarding_version: int = 0
    ui_language: str = "tr"
    last_export_directory: str | None = None
    application_location: str = "user_applications"
    workspace_location: str = "internal"
    theme: str = "dark"
    translation_engine: str = "gemma"


def preferences_path() -> Path:
    return default_database_path().parent / "preferences.json"


def load_preferences(path: str | Path | None = None) -> UserPreferences:
    target = Path(path or preferences_path()).expanduser().resolve()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return UserPreferences()
    if not isinstance(payload, dict):
        return UserPreferences()
    export_kind = str(payload.get("export_kind") or "all")
    if export_kind not in EXPORT_KINDS:
        export_kind = "all"
    try:
        onboarding_version = max(
            0, int(payload.get("onboarding_version", 0) or 0)
        )
    except (TypeError, ValueError):
        onboarding_version = 0
    last_export_directory = payload.get("last_export_directory")
    if not isinstance(last_export_directory, str) or not last_export_directory.startswith("/"):
        last_export_directory = None
    elif "\x00" in last_export_directory or len(last_export_directory) > 4096:
        last_export_directory = None
    return UserPreferences(
        export_kind=export_kind,
        icloud_warning_acknowledged=bool(
            payload.get("icloud_warning_acknowledged", False)
        ),
        onboarding_version=onboarding_version,
        ui_language=(
            str(payload.get("ui_language"))
            if str(payload.get("ui_language") or "") in SUPPORTED_UI_LANGUAGES
            else "tr"
        ),
        last_export_directory=last_export_directory,
        application_location=(
            str(payload.get("application_location"))
            if payload.get("application_location") in {"user_applications", "system_applications"}
            else "user_applications"
        ),
        workspace_location=(
            str(payload.get("workspace_location"))
            if payload.get("workspace_location") in {"internal", "external"}
            else "internal"
        ),
        theme=(
            str(payload.get("theme"))
            if payload.get("theme") in THEMES
            else "dark"
        ),
        translation_engine=(str(payload.get("translation_engine"))
            if payload.get("translation_engine") in TRANSLATION_ENGINES else "gemma"),
    )


def save_preferences(
    preferences: UserPreferences, path: str | Path | None = None
) -> Path:
    if preferences.export_kind not in EXPORT_KINDS:
        raise ValueError("Geçersiz Masaüstü kopyalama seçimi.")
    target = Path(path or preferences_path()).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if preferences.onboarding_version < 0:
        raise ValueError("İlk açılış sürümü negatif olamaz.")
    if preferences.ui_language not in SUPPORTED_UI_LANGUAGES:
        raise ValueError("Geçersiz arayüz dili.")
    if preferences.last_export_directory is not None:
        export_directory = preferences.last_export_directory
        if not export_directory.startswith("/") or "\x00" in export_directory or len(export_directory) > 4096:
            raise ValueError("Geçersiz son dışa aktarma klasörü.")
    if preferences.application_location not in {"user_applications", "system_applications"}:
        raise ValueError("Geçersiz uygulama konumu.")
    if preferences.workspace_location not in {"internal", "external"}:
        raise ValueError("Geçersiz çalışma alanı konumu.")
    if preferences.theme not in THEMES:
        raise ValueError("Geçersiz arayüz teması.")
    if preferences.translation_engine not in TRANSLATION_ENGINES:
        raise ValueError("Geçersiz çeviri motoru.")
    atomic_write_json(target, {"schema_version": 7, **asdict(preferences)})
    try:
        target.chmod(0o600)
    except OSError:
        pass
    return target
