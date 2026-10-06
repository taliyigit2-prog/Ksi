"""Native KSI desktop clients for the shared local service boundary."""

from __future__ import annotations

import fcntl
import json
import os
import re
import signal
import shutil
import sys
import threading
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from ksi_local.bundle_runtime import tool_path, bundle_root, host_architecture

from PySide6.QtCore import (
    QEvent,
    QObject,
    QProcess,
    QProcessEnvironment,
    QStandardPaths,
    Qt,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QIcon,
    QKeySequence,
    QPalette,
    QPixmap,
    QShortcut,
    QColor,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QBoxLayout,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QDoubleSpinBox,
    QSpinBox,
    QStackedWidget,
    QStyleFactory,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ksi_local import __version__
from ksi_local.atomic_files import atomic_write_bytes, atomic_write_json
from ksi_local.cleanup import CleanupResult, cleanup_intermediates, cleanup_inventory
from ksi_local.downloader import (
    BrowserSession,
    Platform,
    SourceURLValidationError,
    validate_browser_session,
    validate_source_url,
)
from ksi_local.document_security import (
    DOCUMENT_SUFFIXES,
    DocumentImportCancelled,
    ImportedDocument,
    import_document_source,
)
from ksi_local.document_translation import validate_document_glossary
from ksi_local.core_service import CoreService
from ksi_local.exporter import (
    ICLOUD_LARGE_EXPORT_BYTES,
    ExportArtifact,
    desktop_uses_icloud,
    export_artifacts,
    export_total_bytes,
    safe_filename,
    select_job_artifacts,
)
from ksi_local.job_store import (
    JobKind,
    JobRecord,
    JobStatus,
    JobStore,
    StageStatus,
    default_database_path,
)
from ksi_local.image_tools import (
    SUPPORTED_IMAGE_SUFFIXES,
    inspect_image,
    plan_resize,
    remove_uniform_background,
    resize_image,
)
from ksi_local.i18n import (
    SUPPORTED_UI_LANGUAGES,
    UI_LANGUAGE_NAMES,
    job_term,
    source_language_name,
    ui_text,
)
from ksi_local.languages import (
    AUTO_LANGUAGE,
    TURKISH_SOURCE_LANGUAGE_NAMES,
    turkish_language_name,
)
from ksi_local.maintenance import (
    AcceptanceReport,
    build_acceptance_report,
    format_acceptance_report,
)
from ksi_local.media import MEDIA_SUFFIXES, PROCESSING_MEDIA_SUFFIXES
from ksi_local.privacy import redact_sensitive_text, safe_source_reference
from ksi_local.preflight import FormatChoice, MediaChoice, PreflightResult, inspect_source
from ksi_local.preferences import ONBOARDING_VERSION, load_preferences, save_preferences
from ksi_local.review_package import (
    ReviewPreview,
    apply_review_package,
    copy_review_package,
    create_review_package,
    preview_review_package,
    review_can_undo,
    undo_last_review,
)
from ksi_local.settings import WorkspacePaths, resolve_workspace
from ksi_local.subtitles import (
    TEXT_SOURCE_SUFFIXES,
    clean_rolling_captions,
    convert_text_source_to_srt,
    discover_best_subtitle,
    read_srt,
)
from ksi_local.subtitle_video import subtitled_output_path
from ksi_local.worker_protocol import JSONLBuffer, parse_worker_event


def _asset_path(name: str) -> Path:
    return Path(__file__).resolve().parents[2] / "assets" / name


def _actionable_message(
    message: str, action: str, *, action_label: str = "Yapılacak"
) -> str:
    raw = str(message).strip()
    if "Yapılacak:" in raw:
        return raw[:900]
    summary = " ".join(raw.split()).strip()[:700] or "İşlem tamamlanamadı."
    return f"{summary}\n\n{action_label}: {action.strip()}"


def _core_error_key(message: str) -> str:
    """Classify legacy/core diagnostics without exposing their raw contents in UI."""
    normalized = str(message).casefold()
    if any(word in normalized for word in ("ssd", "storage", "workspace", "depolama")):
        return "error.storage"
    if any(word in normalized for word in ("no space", "disk full", "yetersiz alan", "boş alan")):
        return "error.space"
    if any(word in normalized for word in ("permission", "read-only", "izin", "yazılabilir")):
        return "error.permission"
    if any(word in normalized for word in ("ollama", "model", "whisper", "chatterbox")):
        return "error.model"
    if any(word in normalized for word in ("network", "ağ", "download", "indirme", "yt-dlp")):
        return "error.network"
    return "error.generic"


@dataclass(frozen=True)
class PendingCommand:
    stage: str
    argv: list[str]


class WorkspaceResolutionBridge(QObject):
    finished = Signal(object, object, bool)


class PreflightBridge(QObject):
    finished = Signal(object, object, object)


class DocumentImportBridge(QObject):
    finished = Signal(object, object, object, object, object)


class ExportBridge(QObject):
    finished = Signal(object, object)


class CleanupBridge(QObject):
    finished = Signal(object, object)


class HealthBridge(QObject):
    finished = Signal(object, object)


@dataclass(frozen=True)
class PreflightSnapshot:
    source: str
    job_kind: str
    source_language: str
    download_only: bool
    want_subtitle: bool
    want_summary: bool
    want_dub: bool
    browser: str | None
    browser_profile: str | None
    udemy_access_confirmed: bool
    document_glossary: str | None = None
    document_summary_profile: str = "standard"
    document_summary_source: str = "auto"
    document_summary_pdf: bool = False


def _human_bytes(value: int) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{amount:.1f} TiB"


def _human_duration(seconds: float) -> str:
    rounded = int(round(seconds))
    hours, remainder = divmod(rounded, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _bundled_glossary_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config/glossary.json"


def _bundled_voice_profile_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config/voice-profile.json"


def _bundled_tool_manifest_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config/tool-manifest.json"


def _chatterbox_python_path() -> Path:
    if bundle_root() is not None:
        return Path(tool_path("chatterbox-python"))
    root = Path(__file__).resolve().parents[2]
    candidates = (
        root / "chatterbox-venv/bin/python",
        root / ".venv-chatterbox/bin/python",
    )
    return next((item for item in candidates if item.is_file()), candidates[0])


def _ocr_helper_path() -> Path:
    if bundle_root() is not None:
        return Path(tool_path("ocr-helper"))
    root = Path(__file__).resolve().parents[2]
    candidates = (root / "bin/KSIOCR", root / "build/KSIOCR")
    return next((item for item in candidates if item.is_file()), candidates[0])


class PreflightDialog(QDialog):
    def __init__(
        self, result: PreflightResult, parent: QWidget | None = None, *, language: str = "tr"
    ) -> None:
        super().__init__(parent)
        self.result = result
        self.ui_language = language
        t = lambda key, **values: ui_text(key, language, **values)
        self._t = t
        self.setWindowTitle(
            t("preflight.document_title")
            if result.document is not None
            else t("preflight.download_title")
        )
        self.setMinimumWidth(620)
        layout = QVBoxLayout(self)
        heading = QLabel(
            t("preflight.document_heading")
            if result.document is not None
            else t("preflight.source_heading")
        )
        heading.setStyleSheet("font-size: 17px; font-weight: 600;")
        layout.addWidget(heading)

        form = QFormLayout()
        self.media_combo = QComboBox()
        self.height_combo = QComboBox()
        if result.document is not None:
            document = result.document
            form.addRow(t("preflight.type"), QLabel(document.format_label))
            filename = QLabel(document.filename)
            filename.setWordWrap(True)
            form.addRow(t("preflight.file"), filename)
            form.addRow(t("preflight.size"), QLabel(_human_bytes(document.size_bytes)))
            form.addRow(
                t("preflight.pages"),
                QLabel(str(document.page_count) if document.page_count is not None else "—"),
            )
            form.addRow(
                t("preflight.blocks"),
                QLabel(str(document.block_count) if document.block_count is not None else "—"),
            )
            detected_language_label = (
                source_language_name(document.detected_language, language)
                if document.detected_language
                else t("preflight.detect_later")
            )
            form.addRow(t("preflight.detected_language"), QLabel(detected_language_label))
            if document.ocr_likely_pages is None:
                ocr = t("preflight.ocr_measure")
            elif document.ocr_likely_pages:
                ocr = t("preflight.ocr_pages", count=document.ocr_likely_pages)
            else:
                ocr = t("preflight.ocr_not_needed")
            form.addRow(t("preflight.ocr"), QLabel(ocr))
            form.addRow(
                t("preflight.security"),
                QLabel(t("preflight.passed") if document.accepted else t("preflight.blocked")),
            )
            form.addRow(t("preflight.hash"), QLabel(document.sha256))
            form.addRow(t("preflight.outputs"), QLabel(", ".join(result.requested_outputs)))
            form.addRow(t("preflight.free_space"), QLabel(_human_bytes(result.free_bytes)))
            layout.addLayout(form)
            choice = result.fixed_budget
            if choice is not None:
                budget = QLabel(
                    t("preflight.safe_copy", download=_human_bytes(choice.estimated_download_bytes), peak=_human_bytes(choice.estimated_peak_bytes), required=_human_bytes(choice.required_bytes))
                )
                budget.setWordWrap(True)
                layout.addWidget(budget)
            if document.warnings:
                warning = QLabel("\n".join(f"• {item}" for item in document.warnings))
                warning.setWordWrap(True)
                warning.setStyleSheet("color: palette(link); font-weight: 600;")
                layout.addWidget(warning)
            if document.blocking_reasons:
                blocked = QLabel(
                    t("preflight.document_blocked", reasons="\n".join(f"• {item}" for item in document.blocking_reasons))
                )
                blocked.setWordWrap(True)
                blocked.setStyleSheet("font-weight: 700;")
                layout.addWidget(blocked)
            tools = QLabel(t("preflight.document_local"))
            tools.setWordWrap(True)
            tools.setStyleSheet("")
            layout.addWidget(tools)
            self.buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
            )
            self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
                t("preflight.approve_import")
            )
            self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(t("dialog.cancel"))
            self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(
                document.accepted and bool(choice and choice.fits)
            )
            self.buttons.accepted.connect(self.accept)
            self.buttons.rejected.connect(self.reject)
            layout.addWidget(self.buttons)
            return

        form.addRow(t("preflight.platform"), QLabel(result.platform))
        if len(result.media_choices) > 1:
            self.media_combo.addItem(t("preflight.choose_video"), None)
            for media in result.media_choices:
                self.media_combo.addItem(
                    f"{t('preflight.video', index=media.index)} · {_human_duration(media.duration_seconds)} · "
                    f"{media.title[:80]}",
                    media.index,
                )
            form.addRow(t("preflight.download_video"), self.media_combo)
        elif result.media_choices:
            media = result.media_choices[0]
            self.media_combo.addItem(t("preflight.video", index=media.index), media.index)
        else:
            self.media_combo.addItem(t("preflight.single_video"), 1)
        self.title_label = QLabel(result.title)
        self.title_label.setWordWrap(True)
        self.uploader_label = QLabel(result.uploader or "—")
        self.duration_label = QLabel(_human_duration(result.duration_seconds))
        self.manual_label = QLabel(", ".join(result.manual_subtitles) or t("preflight.none_suitable"))
        self.automatic_label = QLabel(
            ", ".join(result.automatic_subtitles) or t("preflight.none_suitable")
        )
        self.language_label = QLabel(result.detected_language or t("preflight.unknown"))
        form.addRow(t("preflight.title"), self.title_label)
        form.addRow(t("preflight.uploader"), self.uploader_label)
        form.addRow(t("preflight.duration"), self.duration_label)
        form.addRow(t("preflight.state"), QLabel(result.live_status or t("preflight.recorded")))
        form.addRow(t("preflight.video_count"), QLabel(str(result.entry_count)))
        form.addRow(t("preflight.manual_subtitles"), self.manual_label)
        form.addRow(t("preflight.auto_subtitles"), self.automatic_label)
        form.addRow(t("preflight.detected_language"), self.language_label)
        form.addRow(
            t("preflight.session"),
            QLabel(
                t("preflight.session_used")
                if result.session_used
                else (
                    t("preflight.required") if result.requires_authentication else t("preflight.not_used")
                )
            ),
        )
        form.addRow(t("preflight.outputs"), QLabel(", ".join(result.requested_outputs)))
        form.addRow(t("preflight.free_space"), QLabel(_human_bytes(result.free_bytes)))

        if result.format_choices:
            form.addRow(t("preflight.quality"), self.height_combo)
        else:
            form.addRow(t("preflight.quality"), QLabel(t("preflight.keep_source")))
        self.budget_label = QLabel()
        self.budget_label.setWordWrap(True)
        form.addRow(t("preflight.space_estimate"), self.budget_label)
        layout.addLayout(form)

        if result.warnings:
            warning = QLabel("\n".join(f"• {item}" for item in result.warnings))
            warning.setWordWrap(True)
            warning.setStyleSheet("color: palette(link); font-weight: 600;")
            layout.addWidget(warning)

        tools = QLabel(
            t("preflight.tools", tools=", ".join(result.tool_versions))
            if result.tool_versions
            else t("preflight.local_text")
        )
        tools.setWordWrap(True)
        tools.setStyleSheet("")
        layout.addWidget(tools)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(t("preflight.approve_start"))
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(t("dialog.cancel"))
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.media_combo.currentIndexChanged.connect(self._update_media)
        self.height_combo.currentIndexChanged.connect(self._update_budget)
        self._update_media()

    @property
    def selected_height(self) -> int:
        value = self.height_combo.currentData()
        return int(value) if value is not None else 1080

    @property
    def selected_media_index(self) -> int:
        value = self.media_combo.currentData()
        return int(value) if value is not None else 0

    def _selected_media(self) -> MediaChoice | None:
        return self.result.media_choice(self.selected_media_index)

    def _update_media(self) -> None:
        media = self._selected_media()
        if media is not None:
            self.title_label.setText(media.title)
            self.uploader_label.setText(media.uploader or "—")
            self.duration_label.setText(_human_duration(media.duration_seconds))
            self.manual_label.setText(
                ", ".join(media.manual_subtitles) or self._t("preflight.none_suitable")
            )
            self.automatic_label.setText(
                ", ".join(media.automatic_subtitles) or self._t("preflight.none_suitable")
            )
            self.language_label.setText(media.detected_language or self._t("preflight.unknown"))
            choices = media.format_choices
            default_height = media.default_height
        elif len(self.result.media_choices) > 1:
            self.title_label.setText(self._t("preflight.choose_video"))
            self.uploader_label.setText("—")
            self.duration_label.setText("—")
            self.manual_label.setText("—")
            self.automatic_label.setText("—")
            self.language_label.setText("—")
            choices = ()
            default_height = None
        else:
            choices = self.result.format_choices
            default_height = self.result.default_height
        self.height_combo.blockSignals(True)
        self.height_combo.clear()
        for choice in choices:
            suffix = "" if choice.fits else self._t("preflight.insufficient")
            self.height_combo.addItem(f"{choice.height}p{suffix}", choice.height)
        default_index = self.height_combo.findData(default_height)
        self.height_combo.setCurrentIndex(max(default_index, 0))
        self.height_combo.blockSignals(False)
        self._update_budget()

    def _selected_choice(self) -> FormatChoice | None:
        if self.result.format_choices:
            if len(self.result.media_choices) > 1 and self.selected_media_index == 0:
                return None
            return self.result.choice(
                self.selected_height,
                media_index=max(self.selected_media_index, 1),
            )
        return self.result.fixed_budget

    def _update_budget(self) -> None:
        choice = self._selected_choice()
        ok_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        if choice is None:
            if len(self.result.media_choices) > 1 and self.selected_media_index == 0:
                self.budget_label.setText(self._t("preflight.select_first"))
                ok_button.setEnabled(False)
            else:
                self.budget_label.setText(self._t("preflight.no_large_download"))
                ok_button.setEnabled(True)
            return
        self.budget_label.setText(self._t("preflight.budget", download=_human_bytes(choice.estimated_download_bytes), peak=_human_bytes(choice.estimated_peak_bytes), required=_human_bytes(choice.required_bytes)))
        ok_button.setEnabled(choice.fits)


class SubtitleReviewDialog(QDialog):
    def __init__(
        self,
        translated_path: Path,
        *,
        source_path: Path | None = None,
        quality_path: Path | None = None,
        language: str = "tr",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        t = lambda key, **values: ui_text(key, language, **values)
        self.setWindowTitle(t("review.subtitle_title"))
        self.resize(980, 680)
        layout = QVBoxLayout(self)
        translated = read_srt(translated_path)
        source = (
            clean_rolling_captions(read_srt(source_path))
            if source_path is not None and source_path.is_file()
            else []
        )
        quality: dict[str, object] = {}
        if quality_path is not None and quality_path.is_file():
            try:
                candidate = json.loads(quality_path.read_text(encoding="utf-8"))
                if isinstance(candidate, dict):
                    quality = candidate
            except (OSError, json.JSONDecodeError):
                pass
        issue_map: dict[int, list[str]] = {}
        issues = quality.get("issues")
        if isinstance(issues, list):
            for issue in issues:
                if not isinstance(issue, dict):
                    continue
                try:
                    cue_index = int(issue.get("cue_index", 0))
                except (TypeError, ValueError):
                    continue
                message = str(issue.get("message") or t("review.needs_check"))
                level = t("review.error") if issue.get("level") == "error" else t("review.warning")
                issue_map.setdefault(cue_index, []).append(f"{level}: {message}")
        if quality:
            summary = QLabel(t("review.lines", count=len(translated), errors=int(quality.get("error_count", 0)), warnings=int(quality.get("warning_count", 0))))
        else:
            summary = QLabel(t("review.no_subtitle_quality", count=len(translated)))
        summary.setWordWrap(True)
        layout.addWidget(summary)

        table = QTableWidget(len(translated), 4)
        table.setHorizontalHeaderLabels([t("review.time"), t("review.source"), t("review.turkish"), t("review.check")])
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        for row, target in enumerate(translated):
            source_text = source[row].text if row < len(source) else "—"
            checks = "\n".join(issue_map.get(row + 1, ())) or "✓"
            values = (
                f"{target.start} → {target.end}",
                source_text,
                target.text,
                checks,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                table.setItem(row, column, item)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        table.resizeRowsToContents()
        layout.addWidget(table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText(t("dialog.close"))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class DocumentTranslationReviewDialog(QDialog):
    def __init__(
        self,
        translated_path: Path,
        *,
        quality_path: Path,
        language: str = "tr",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        t = lambda key, **values: ui_text(key, language, **values)
        self.setWindowTitle(t("review.document_title"))
        self.resize(1080, 720)
        layout = QVBoxLayout(self)
        if translated_path.stat().st_size > 100 * 1024 * 1024:
            raise ValueError("Türkçe belge çevirisi güvenli görüntüleme sınırını aşıyor.")
        records = [
            json.loads(line)
            for line in translated_path.read_text(encoding="utf-8").splitlines()
        ]
        quality = json.loads(quality_path.read_text(encoding="utf-8"))
        if not all(isinstance(item, dict) for item in records) or not isinstance(quality, dict):
            raise ValueError("Türkçe belge inceleme çıktısı geçersiz.")
        issue_map: dict[str, list[str]] = {}
        for issue in quality.get("issues", []):
            if isinstance(issue, dict):
                block_id = str(issue.get("block_id") or "")
                level = t("review.error") if issue.get("level") == "error" else t("review.warning")
                issue_map.setdefault(block_id, []).append(
                    f"{level}: {issue.get('message') or t('review.needs_check')}"
                )
        summary = QLabel(t("review.document_summary", count=len(records), translated=int(quality.get("translated_block_count", 0)), preserved=int(quality.get("preserved_block_count", 0)), errors=int(quality.get("error_count", 0)), warnings=int(quality.get("warning_count", 0))))
        summary.setWordWrap(True)
        layout.addWidget(summary)
        table = QTableWidget(len(records), 5)
        table.setHorizontalHeaderLabels([t("review.block"), t("review.language_location"), t("review.source"), t("review.turkish"), t("review.check")])
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        for row, record in enumerate(records):
            block_id = str(record.get("id") or "—")
            location = ", ".join(
                f"{key}={value}" for key, value in (record.get("location") or {}).items()
            )
            values = (
                f"{block_id}\n{record.get('block_type') or '—'}",
                f"{record.get('source_language') or '—'}\n{location}",
                str(record.get("source_text") or ""),
                str(record.get("translated_text") or ""),
                "\n".join(issue_map.get(block_id, ())) or "✓",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                table.setItem(row, column, item)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        for column in (2, 3, 4):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        table.resizeRowsToContents()
        layout.addWidget(table, 1)
        note = QLabel(t("review.document_note"))
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText(t("dialog.close"))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class SummaryReviewDialog(QDialog):
    def __init__(
        self,
        summary_path: Path,
        *,
        quality_path: Path | None = None,
        language: str = "tr",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        t = lambda key, **values: ui_text(key, language, **values)
        self.setWindowTitle(t("review.summary_title"))
        self.resize(860, 700)
        layout = QVBoxLayout(self)
        if summary_path.stat().st_size > 5 * 1024 * 1024:
            raise ValueError("Özet dosyası güvenli görüntüleme sınırını aşıyor.")
        summary = summary_path.read_text(encoding="utf-8")
        quality: dict[str, object] = {}
        if quality_path is not None and quality_path.is_file():
            try:
                candidate = json.loads(quality_path.read_text(encoding="utf-8"))
                if isinstance(candidate, dict):
                    quality = candidate
            except (OSError, json.JSONDecodeError):
                pass
        if quality:
            statement_count = int(quality.get("statement_count", 0))
            source_coverage = float(quality.get("source_coverage_percent", 0))
            status = "✓" if quality.get("passed") is True else t("review.needs_check")
            if "evidence_coverage_percent" in quality:
                evidence_coverage = float(quality.get("evidence_coverage_percent", 0))
                location_coverage = float(quality.get("location_coverage_percent", 0))
                label = QLabel(
                    t(
                        "review.summary_quality_document",
                        status=status,
                        statements=statement_count,
                        source=source_coverage,
                        evidence=evidence_coverage,
                        location=location_coverage,
                    )
                )
            else:
                timestamp_coverage = float(quality.get("timestamp_coverage_percent", 0))
                label = QLabel(
                    t(
                        "review.summary_quality_video",
                        status=status,
                        statements=statement_count,
                        source=source_coverage,
                        timestamps=timestamp_coverage,
                    )
                )
        else:
            label = QLabel(t("review.summary_no_quality"))
        label.setWordWrap(True)
        layout.addWidget(label)
        viewer = QPlainTextEdit()
        viewer.setReadOnly(True)
        viewer.setPlainText(summary)
        layout.addWidget(viewer, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText(t("dialog.close"))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class SystemStatusDialog(QDialog):
    full_verification_requested = Signal()

    def __init__(
        self,
        report: AcceptanceReport,
        parent: QWidget | None = None,
        *,
        language: str = "tr",
    ) -> None:
        super().__init__(parent)
        self.report = report
        t = lambda key, **values: ui_text(key, language, **values)
        self.setWindowTitle(t("system.title"))
        self.resize(720, 620)
        layout = QVBoxLayout(self)
        heading = QLabel(t("system.ready") if report.passed else t("system.check"))
        heading.setStyleSheet("font-size: 22px; font-weight: 700;")
        layout.addWidget(heading)
        explanation = QLabel(t("system.explanation"))
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        viewer = QPlainTextEdit()
        viewer.setReadOnly(True)
        viewer.setPlainText(format_acceptance_report(report, language=language))
        layout.addWidget(viewer, 1)
        note = QLabel(t("system.note"))
        note.setWordWrap(True)
        note.setStyleSheet("")
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText(t("system.close"))
        verify = buttons.addButton(
            t("system.verify"), QDialogButtonBox.ButtonRole.ActionRole
        )
        verify.setEnabled(not report.full_model_verification)
        verify.clicked.connect(self._request_full_verification)
        open_ssd = buttons.addButton(
            t("system.open_storage"), QDialogButtonBox.ButtonRole.ActionRole
        )
        open_ssd.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(report.workspace))
        )
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _request_full_verification(self) -> None:
        self.accept()
        self.full_verification_requested.emit()


class FirstRunWizard(QDialog):
    """One-page first-launch guide backed by the normal local health report."""

    def __init__(
        self,
        report: AcceptanceReport,
        parent: QWidget | None = None,
        *,
        language: str = "tr",
        application_location: str = "user_applications",
        workspace_location: str = "internal",
    ) -> None:
        super().__init__(parent)
        self.report = report
        t = lambda key, **values: ui_text(key, language, **values)
        self.setWindowTitle(t("first.title"))
        self.setModal(True)
        self.resize(610, 520)
        layout = QVBoxLayout(self)
        heading = QLabel(
            t("first.ready")
            if report.passed
            else t("first.check")
        )
        heading.setStyleSheet("font-size: 23px; font-weight: 700;")
        heading.setAccessibleName(t("first.result_accessible"))
        layout.addWidget(heading)
        explanation = QLabel(t("first.explanation"))
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        location_group = QGroupBox(t("first.locations"))
        location_layout = QFormLayout(location_group)
        self.application_location = QComboBox()
        self.application_location.addItem(t("first.app_user"), "user_applications")
        self.application_location.addItem(t("first.app_system"), "system_applications")
        self.workspace_location = QComboBox()
        self.workspace_location.addItem(t("first.workspace_internal"), "internal")
        self.workspace_location.addItem(t("first.workspace_external"), "external")
        self.application_location.setCurrentIndex(
            max(0, self.application_location.findData(application_location))
        )
        self.workspace_location.setCurrentIndex(
            max(0, self.workspace_location.findData(workspace_location))
        )
        location_layout.addRow(t("first.app_location"), self.application_location)
        location_layout.addRow(t("first.workspace_location"), self.workspace_location)
        layout.addWidget(location_group)

        usage = report.workspace_free_bytes / 1024**3
        tools_ready = bool(report.tool_integrity_ok and report.health.youtube_js_ready)
        models_ready = all(model.ready for model in report.models)
        package_ready = bool(
            report.app_bundle.exists
            and report.app_bundle.signed
            and report.app_bundle.native_arm64_only
        )
        checks = (
            (
                usage >= 20,
                t("first.storage"),
                t("first.storage_detail", workspace=report.workspace, free=usage),
            ),
            (
                tools_ready,
                t("first.tools"),
                t("first.tools_ready")
                if tools_ready
                else t("first.tools_missing"),
            ),
            (
                models_ready,
                t("first.models"),
                t("first.models_ready")
                if models_ready
                else t("first.models_missing"),
            ),
            (
                package_ready,
                t("first.package"),
                t("first.package_ready")
                if package_ready
                else t("first.package_missing"),
            ),
            (
                True,
                t("first.privacy"),
                t("first.privacy_detail"),
            ),
        )
        check_group = QGroupBox(t("first.checks"))
        check_layout = QVBoxLayout(check_group)
        for ready, title, detail in checks:
            row = QLabel(f"{'✓' if ready else '!'}  {title}\n    {detail}")
            row.setWordWrap(True)
            row.setAccessibleName(
                f"{title}: {t('first.ready_state') if ready else t('first.check_state')}"
            )
            check_layout.addWidget(row)
        layout.addWidget(check_group, 1)

        next_step = QLabel(t("first.next"))
        next_step.setWordWrap(True)
        next_step.setStyleSheet("font-weight: 600;")
        layout.addWidget(next_step)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(t("first.use"))
        buttons.button(QDialogButtonBox.StandardButton.Ok).setAccessibleName(
            t("first.use_accessible")
        )
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


class DropLineEdit(QLineEdit):
    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        urls = event.mimeData().urls()
        if urls:
            self.setText(urls[0].toLocalFile() or urls[0].toString())
            event.acceptProposedAction()


_IMAGE_DIALOG_TEXT = {
    "tr": ("Görsel Araçları", "Piksel", "Yüzde", "Genişlik", "Yükseklik", "Oranı koru", "Arka planı kaldır", "Ürün gölgesi", "Yeni dosyayı kaydet", "Görsel kaydedildi"),
    "ru": ("Инструменты изображения", "Пиксели", "Процент", "Ширина", "Высота", "Сохранять пропорции", "Удалить фон", "Тень товара", "Сохранить новый файл", "Изображение сохранено"),
    "en": ("Image Tools", "Pixels", "Percent", "Width", "Height", "Keep aspect ratio", "Remove background", "Product shadow", "Save new file", "Image saved"),
    "es": ("Herramientas de imagen", "Píxeles", "Porcentaje", "Anchura", "Altura", "Mantener proporción", "Quitar fondo", "Sombra de producto", "Guardar archivo nuevo", "Imagen guardada"),
    "de": ("Bildwerkzeuge", "Pixel", "Prozent", "Breite", "Höhe", "Seitenverhältnis behalten", "Hintergrund entfernen", "Produktschatten", "Neue Datei speichern", "Bild gespeichert"),
    "fr": ("Outils d’image", "Pixels", "Pourcentage", "Largeur", "Hauteur", "Conserver les proportions", "Supprimer l’arrière-plan", "Ombre du produit", "Enregistrer un nouveau fichier", "Image enregistrée"),
    "it": ("Strumenti immagine", "Pixel", "Percentuale", "Larghezza", "Altezza", "Mantieni proporzioni", "Rimuovi sfondo", "Ombra prodotto", "Salva nuovo file", "Immagine salvata"),
    "zh": ("图像工具", "像素", "百分比", "宽度", "高度", "保持宽高比", "移除背景", "产品阴影", "保存新文件", "图像已保存"),
}


class ImageToolsDialog(QDialog):
    """Small drag/drop destination for the safe Phase 32 resize workflow."""

    def __init__(self, source: str | Path, ui_language: str = "tr", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.source = Path(source).expanduser().resolve()
        self.inspection = inspect_image(self.source)
        copy = _IMAGE_DIALOG_TEXT.get(ui_language, _IMAGE_DIALOG_TEXT["en"])
        self.setWindowTitle(copy[0])
        self.resize(520, 560)
        layout = QVBoxLayout(self)
        preview = QLabel()
        preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview.setPixmap(
            QPixmap(str(self.source)).scaled(
                440, 260, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
        )
        preview.setAccessibleName(copy[0])
        layout.addWidget(preview)
        details = QLabel(
            f"{self.source.name} · {self.inspection.width} × {self.inspection.height} · "
            f"{self.inspection.estimated_working_bytes / 1024**2:.0f} MiB"
        )
        details.setWordWrap(True)
        layout.addWidget(details)
        form = QFormLayout()
        self.mode = QComboBox()
        self.mode.addItem(copy[1], "pixels")
        self.mode.addItem(copy[2], "percent")
        self.width = QSpinBox()
        self.width.setRange(1, 20000)
        self.width.setValue(self.inspection.width)
        self.height = QSpinBox()
        self.height.setRange(1, 20000)
        self.height.setValue(self.inspection.height)
        self.percent = QDoubleSpinBox()
        self.percent.setRange(1, 400)
        self.percent.setValue(100)
        self.percent.setSuffix(" %")
        self.percent.setVisible(False)
        self.keep_aspect = QCheckBox(copy[5])
        self.keep_aspect.setChecked(True)
        self.output_format = QComboBox()
        for label in ("PNG", "WEBP", "JPEG"):
            self.output_format.addItem(label, label)
        self.remove_background = QCheckBox(copy[6])
        self.product_shadow = QCheckBox(copy[7])
        self.product_shadow.setEnabled(False)
        self.remove_background.toggled.connect(self.product_shadow.setEnabled)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        form.addRow(copy[1], self.mode)
        form.addRow(copy[3], self.width)
        form.addRow(copy[4], self.height)
        form.addRow(copy[2], self.percent)
        form.addRow("", self.keep_aspect)
        form.addRow("Format", self.output_format)
        form.addRow("", self.remove_background)
        form.addRow("", self.product_shadow)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(copy[8])
        buttons.accepted.connect(lambda: self._save(copy[9]))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _mode_changed(self) -> None:
        pixels = self.mode.currentData() == "pixels"
        self.width.setVisible(pixels)
        self.height.setVisible(pixels)
        self.percent.setVisible(not pixels)

    def _save(self, completed_text: str) -> None:
        fmt = "PNG" if self.remove_background.isChecked() else str(self.output_format.currentData())
        suffix = {"PNG": ".png", "WEBP": ".webp", "JPEG": ".jpg"}[fmt]
        selected, _ = QFileDialog.getSaveFileName(
            self,
            self.windowTitle(),
            str(self.source.with_name(f"{self.source.stem}-ksi{suffix}")),
            f"{fmt} (*{suffix})",
        )
        if not selected:
            return
        try:
            if self.remove_background.isChecked():
                output = remove_uniform_background(
                    self.source, selected, product_shadow=self.product_shadow.isChecked()
                )
            else:
                kwargs = (
                    {"percent": self.percent.value()}
                    if self.mode.currentData() == "percent"
                    else {"width": self.width.value()}
                    if self.keep_aspect.isChecked()
                    else {"width": self.width.value(), "height": self.height.value()}
                )
                plan = plan_resize(
                    self.source,
                    **kwargs,
                    lock_aspect=self.keep_aspect.isChecked(),
                    output_format=fmt,
                    lossless=fmt != "JPEG",
                    super_resolution_pilot=(
                        self.width.value() > self.inspection.width
                        or self.height.value() > self.inspection.height
                        or self.percent.value() > 100
                    ),
                )
                output = resize_image(plan, selected)
        except (OSError, RuntimeError, ValueError) as error:
            QMessageBox.critical(self, self.windowTitle(), redact_sensitive_text(str(error)))
            return
        QMessageBox.information(self, self.windowTitle(), f"{completed_text}: {output.name}")
        self.accept()

class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("KSI Local Studio")
        self.resize(1180, 820)
        self.setMinimumSize(860, 600)
        application = QApplication.instance()
        self._system_palette = QPalette(application.palette()) if application else QPalette()
        self._system_style_name = application.style().objectName() if application else "macos"
        self.workspace: WorkspacePaths | None = None
        self.store = JobStore()
        self.core = CoreService(store=self.store)
        self.preferences = load_preferences()
        self.process: QProcess | None = None
        self.pending: list[PendingCommand] = []
        self.current_command: PendingCommand | None = None
        self.current_job: Path | None = None
        self.current_job_id: str | None = None
        self.source_subtitle: Path | None = None
        self.source_media: Path | None = None
        self.cancel_requested = False
        self.waiting_for_ssd = False
        self.failure_handled = False
        self.closing = False
        self.workspace_initialized = False
        self.startup_interrupted_job_ids: tuple[str, ...] = ()
        self.workspace_resolution_pending = False
        self.workspace_poll_failures = 0
        self.workspace_bridge = WorkspaceResolutionBridge(self)
        self.workspace_bridge.finished.connect(self._workspace_resolution_finished)
        self.workspace_thread: threading.Thread | None = None
        self.preflight_pending = False
        self.preflight_bridge = PreflightBridge(self)
        self.preflight_bridge.finished.connect(self._preflight_finished)
        self.preflight_thread: threading.Thread | None = None
        self.preflight_existing_job: JobRecord | None = None
        self.maintenance_pending = False
        self.document_import_bridge = DocumentImportBridge(self)
        self.document_import_bridge.finished.connect(self._document_import_finished)
        self.document_import_thread: threading.Thread | None = None
        self.document_import_cancel = threading.Event()
        self.export_bridge = ExportBridge(self)
        self.export_bridge.finished.connect(self._export_finished)
        self.export_thread: threading.Thread | None = None
        self.cleanup_bridge = CleanupBridge(self)
        self.cleanup_bridge.finished.connect(self._cleanup_finished)
        self.cleanup_thread: threading.Thread | None = None
        self.health_bridge = HealthBridge(self)
        self.health_bridge.finished.connect(self._system_status_finished)
        self.health_thread: threading.Thread | None = None
        self.health_dialog_mode = "system"
        self.first_run_dialog_active = False
        self.first_run_pending = (
            self.preferences.onboarding_version < ONBOARDING_VERSION
        )
        self.stdout_buffer = JSONLBuffer()
        self.stderr_buffer = JSONLBuffer()
        self.browser_profile: Path | None = None
        self.browser_profile_platform: Platform | None = None
        self.document_glossary_path: Path | None = None

        central = QWidget()
        layout = QVBoxLayout(central)
        title = QLabel("KSI Local Studio")
        title.setStyleSheet("font-size: 28px; font-weight: 700;")
        subtitle = QLabel(
            "Video bağlantısını veya yerel video, altyazı ve belge dosyasını ekle."
        )
        subtitle.setStyleSheet("")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        source_row = QHBoxLayout()
        self.source = DropLineEdit()
        self.source.setPlaceholderText(
            "YouTube / X / Udemy ya da PDF/DOCX/MD/TXT/video/altyazı dosyası"
        )
        self.source.textChanged.connect(self._source_changed)
        browse = QPushButton("Dosya Seç…")
        browse.clicked.connect(self._browse)
        source_row.addWidget(self.source, 1)
        source_row.addWidget(browse)
        layout.addLayout(source_row)

        form = QFormLayout()
        self.job_kind = QComboBox()
        self.job_kind.addItem("Otomatik belirle", "auto")
        self.job_kind.addItem("Video veya konuşma metni", "video")
        self.job_kind.addItem("Belge", "document")
        self.job_kind.currentIndexChanged.connect(self._job_kind_changed)
        form.addRow("İş türü", self.job_kind)
        self.language = QComboBox()
        self.language.addItem("Dili otomatik algıla", AUTO_LANGUAGE)
        for code, name in TURKISH_SOURCE_LANGUAGE_NAMES.items():
            self.language.addItem(name, code)
        form.addRow("Kaynak dil", self.language)
        self.document_glossary_widget = QWidget()
        glossary_row = QHBoxLayout(self.document_glossary_widget)
        glossary_row.setContentsMargins(0, 0, 0, 0)
        self.document_glossary_button = QPushButton("JSON sözlük seç…")
        self.document_glossary_button.clicked.connect(self._select_document_glossary)
        self.document_glossary_clear = QPushButton("Temizle")
        self.document_glossary_clear.clicked.connect(self._clear_document_glossary)
        self.document_glossary_label = QLabel("İsteğe bağlı")
        self.document_glossary_label.setStyleSheet("")
        glossary_row.addWidget(self.document_glossary_button)
        glossary_row.addWidget(self.document_glossary_clear)
        glossary_row.addWidget(self.document_glossary_label, 1)
        self.document_glossary_title = QLabel("Belge sözlüğü")
        form.addRow(self.document_glossary_title, self.document_glossary_widget)
        self.document_summary_profile = QComboBox()
        self.document_summary_profile.addItem("Kısa", "short")
        self.document_summary_profile.addItem("Standart", "standard")
        self.document_summary_profile.addItem("Ayrıntılı", "detailed")
        self.document_summary_profile.setCurrentIndex(1)
        self.document_summary_profile_title = QLabel("Özet uzunluğu")
        form.addRow(self.document_summary_profile_title, self.document_summary_profile)
        self.document_summary_source = QComboBox()
        self.document_summary_source.addItem(
            "Otomatik (Türkçe çeviriyi tercih et)", "auto"
        )
        self.document_summary_source.addItem("Kaynak belge metni", "source")
        self.document_summary_source.addItem("Doğrulanmış Türkçe çeviri", "translation")
        self.document_summary_source.currentIndexChanged.connect(
            self._document_summary_source_changed
        )
        self.document_summary_source_title = QLabel("Özet kaynağı")
        form.addRow(self.document_summary_source_title, self.document_summary_source)
        self.document_summary_pdf = QCheckBox("Okunaklı PDF özeti de oluştur")
        self.document_summary_pdf_title = QLabel("Ek çıktı")
        form.addRow(self.document_summary_pdf_title, self.document_summary_pdf)
        layout.addLayout(form)

        session_row = QHBoxLayout()
        self.use_browser_session = QCheckBox("Bu iş için ayrı X/Udemy oturumunu kullan")
        self.browser_kind = QComboBox()
        self.browser_kind.addItem("Chrome", "chrome")
        self.browser_kind.addItem("Firefox", "firefox")
        self.select_profile_button = QPushButton("Ayrı Profil Seç…")
        self.select_profile_button.clicked.connect(self._select_browser_profile)
        session_row.addWidget(self.use_browser_session)
        session_row.addWidget(self.browser_kind)
        session_row.addWidget(self.select_profile_button)
        session_row.addStretch(1)
        layout.addLayout(session_row)
        self.browser_profile_label = QLabel(
            "Yalnız korumalı X içeriği için; ana profil otomatik aranmaz."
        )
        self.browser_profile_label.setStyleSheet("")
        layout.addWidget(self.browser_profile_label)
        self.udemy_access_confirmed = QCheckBox(
            "Bu tek Udemy dersine erişim hakkım var (deneysel)"
        )
        layout.addWidget(self.udemy_access_confirmed)

        self.want_subtitle = QCheckBox("Türkçe altyazı üret")
        self.want_subtitle.setChecked(True)
        self.want_subtitle.toggled.connect(self._document_translation_option_changed)
        self.want_summary = QCheckBox("Türkçe yazılı özet üret")
        self.want_summary.setChecked(True)
        self.want_summary.toggled.connect(self._document_summary_options_changed)
        self.download_only = QCheckBox("Sadece indir")
        self.download_only.toggled.connect(self._download_only_toggled)
        self.want_dub = QCheckBox("Türkçe dublaj üret")
        self._source_changed(self.source.text())
        layout.addWidget(self.download_only)
        layout.addWidget(self.want_subtitle)
        layout.addWidget(self.want_summary)
        layout.addWidget(self.want_dub)

        actions = QHBoxLayout()
        self.start_button = QPushButton("İncele")
        self.start_button.setDefault(True)
        self.start_button.clicked.connect(self._start)
        self.cancel_button = QPushButton("■  Çalışan İşi Durdur")
        self.cancel_button.setObjectName("stopButton")
        self.cancel_button.setMinimumHeight(38)
        self.cancel_button.setAccessibleName("Çalışan işi durdur")
        self.cancel_button.setAccessibleDescription(
            "Çalışan aşamayı durdurur; tamamlanan dosyalar ve güvenli devam noktaları korunur"
        )
        self.cancel_button.setToolTip(
            "Çalışan aşamayı durdurur. Tamamlanan indirme ve güvenli devam noktaları korunur."
        )
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel)
        self.review_button = QPushButton("Altyazıyı İncele")
        self.review_button.setEnabled(False)
        self.review_button.clicked.connect(self._review_subtitle)
        self.summary_review_button = QPushButton("Özeti İncele")
        self.summary_review_button.setEnabled(False)
        self.summary_review_button.clicked.connect(self._review_summary)
        self.open_dub_button = QPushButton("Dublajı Aç")
        self.open_dub_button.setEnabled(False)
        self.open_dub_button.clicked.connect(self._open_dub)
        self.export_kind = QComboBox()
        self.export_kind.addItem("Tüm sonuçlar", "all")
        self.export_kind.addItem("Final video", "video")
        self.export_kind.addItem("Türkçe özet", "summary")
        self.export_kind.addItem("Türkçe altyazı", "subtitle")
        self.export_kind.addItem("Türkçe belge çevirisi", "translation")
        export_index = self.export_kind.findData(self.preferences.export_kind)
        self.export_kind.setCurrentIndex(max(export_index, 0))
        self.export_kind.currentIndexChanged.connect(self._export_kind_changed)
        self.export_target = QComboBox()
        self.export_target.addItem("Masaüstü", "desktop")
        self.export_target.addItem("Belgeler", "documents")
        self.export_target.addItem("Başka klasör", "custom")
        self.export_target.currentIndexChanged.connect(self._export_target_changed)
        self.custom_export_directory: Path | None = None
        self.export_directory_button = QPushButton("Klasör Seç…")
        self.export_directory_button.setEnabled(False)
        self.export_directory_button.clicked.connect(self._select_export_directory)
        self.export_button = QPushButton("Mac'e Kopyala")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self._export)
        actions.addWidget(self.start_button)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.review_button)
        actions.addWidget(self.summary_review_button)
        actions.addWidget(self.open_dub_button)
        layout.addLayout(actions)
        export_row = QHBoxLayout()
        export_row.addStretch(1)
        export_row.addWidget(QLabel("Mac çıktısı"))
        export_row.addWidget(self.export_kind)
        export_row.addWidget(self.export_target)
        export_row.addWidget(self.export_directory_button)
        export_row.addWidget(self.export_button)
        layout.addLayout(export_row)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.status = QLabel("SSD denetleniyor…")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("İşlem ayrıntıları burada görünür.")
        layout.addWidget(self.log, 1)

        history_title = QLabel("İş geçmişi")
        history_title.setStyleSheet("font-weight: 600;")
        layout.addWidget(history_title)
        self.history = QTableWidget(0, 6)
        self.history.setHorizontalHeaderLabels(
            ["İş", "Kaynak", "Tür / Dil", "Çıktılar", "Durum", "Güncellendi"]
        )
        self.history.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.history.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.history.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history.setMaximumHeight(155)
        header = self.history.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.history.itemSelectionChanged.connect(self._history_selection_changed)
        layout.addWidget(self.history)
        history_actions = QHBoxLayout()
        history_actions.addStretch(1)
        self.retry_button = QPushButton("Seçili İşi Yeniden Dene")
        self.retry_button.setEnabled(False)
        self.retry_button.clicked.connect(self._retry_selected)
        self.add_outputs_button = QPushButton("Eksik Çıktıları Ekle")
        self.add_outputs_button.setEnabled(False)
        self.add_outputs_button.clicked.connect(self._add_outputs_selected)
        self.cleanup_button = QPushButton("SSD Ara Dosyalarını Temizle")
        self.cleanup_button.setEnabled(False)
        self.cleanup_button.clicked.connect(self._cleanup_selected)
        history_actions.addWidget(self.cleanup_button)
        history_actions.addWidget(self.add_outputs_button)
        history_actions.addWidget(self.retry_button)
        layout.addLayout(history_actions)

        system_actions = QHBoxLayout()
        self.system_status_button = QPushButton("Sistem Durumu")
        self.system_status_button.clicked.connect(self._open_system_status)
        system_actions.addWidget(self.system_status_button)
        system_actions.addStretch(1)
        layout.addLayout(system_actions)

        self.external_review_label = QLabel("İsteğe bağlı harici inceleme")
        self.external_review_label.setStyleSheet(
            "font-weight: 600; margin-top: 6px;"
        )
        self.external_review_label.setToolTip(
            "Temel yerel akış için gerekli değildir ve kendiliğinden internet bağlantısı kurmaz."
        )
        layout.addWidget(self.external_review_label)
        review_actions = QHBoxLayout()
        review_actions.addStretch(1)
        self.create_codex_package_button = QPushButton("İnceleme Paketi Oluştur")
        self.create_codex_package_button.setEnabled(False)
        self.create_codex_package_button.setToolTip(
            "Yalnız metinleri dışarı aktarır; Codex veya başka bir düzenleyici "
            "kullanmak isteğe bağlıdır."
        )
        self.create_codex_package_button.clicked.connect(self._create_codex_package)
        self.import_codex_package_button = QPushButton("Düzeltilmiş Paketi Al")
        self.import_codex_package_button.setEnabled(False)
        self.import_codex_package_button.clicked.connect(self._import_codex_package)
        self.undo_codex_review_button = QPushButton("Son Düzeltmeyi Geri Al")
        self.undo_codex_review_button.setEnabled(False)
        self.undo_codex_review_button.clicked.connect(self._undo_codex_review)
        review_actions.addWidget(self.create_codex_package_button)
        review_actions.addWidget(self.import_codex_package_button)
        review_actions.addWidget(self.undo_codex_review_button)
        layout.addLayout(review_actions)

        # Phase 18 presents the existing, proven controls in a simpler shell.
        # Re-adding a widget to a new layout safely removes it from the temporary
        # construction layout above, while preserving every signal connection.
        content = QWidget()
        root_layout = QVBoxLayout(content)
        root_layout.setContentsMargins(18, 16, 18, 18)
        root_layout.setSpacing(12)

        logo_path = _asset_path("ksi-logo.png")
        if logo_path.is_file():
            icon = QIcon(str(logo_path))
            self.setWindowIcon(icon)
            application = QApplication.instance()
            if application is not None:
                application.setWindowIcon(icon)
        header_layout = QHBoxLayout()
        logo_label = QLabel()
        if logo_path.is_file():
            logo_label.setPixmap(
                QPixmap(str(logo_path)).scaled(
                    58,
                    58,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        logo_label.setFixedSize(62, 62)
        logo_label.setAccessibleName("KSI Local Studio logosu")
        self.header_subtitle = subtitle
        header_copy = QVBoxLayout()
        title.setText("KSI Local Studio")
        title.setStyleSheet("font-size: 26px; font-weight: 750;")
        subtitle.setText(self._t("header.subtitle"))
        subtitle.setStyleSheet("")
        subtitle.setWordWrap(True)
        header_copy.addWidget(title)
        header_copy.addWidget(subtitle)
        header_layout.addWidget(logo_label)
        header_layout.addLayout(header_copy, 1)
        short_version = ".".join(__version__.split(".")[:2])
        self.local_badge = QLabel(f"YEREL • {short_version}")
        self.local_badge.setObjectName("localBadge")
        self.local_badge.setAccessibleName("Yerel çalışma göstergesi")
        language_header = QGridLayout()
        self.ui_language_label = QLabel("Arayüz")
        self.ui_language = QComboBox()
        for code in SUPPORTED_UI_LANGUAGES:
            self.ui_language.addItem(UI_LANGUAGE_NAMES[code], code)
        selected_ui_language = self.ui_language.findData(self.preferences.ui_language)
        self.ui_language.setCurrentIndex(max(selected_ui_language, 0))
        self.ui_language.currentIndexChanged.connect(self._ui_language_changed)
        self.theme_label = QLabel("Tema")
        self.theme = QComboBox()
        self.theme.addItem("Sistem", "system")
        self.theme.addItem("Açık", "light")
        self.theme.addItem("Koyu", "dark")
        selected_theme = self.theme.findData(self.preferences.theme)
        self.theme.setCurrentIndex(max(selected_theme, 0))
        self.theme.currentIndexChanged.connect(self._theme_changed)
        language_header.addWidget(self.local_badge, 0, 0, 1, 2)
        language_header.addWidget(self.ui_language_label, 1, 0)
        language_header.addWidget(self.theme_label, 1, 1)
        language_header.addWidget(self.ui_language, 2, 0)
        language_header.addWidget(self.theme, 2, 1)
        header_layout.addLayout(language_header)
        root_layout.addLayout(header_layout)

        self.main_columns = None
        self.tabs = QTabWidget()
        self.tabs.setObjectName("mainTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.setUsesScrollButtons(True)

        self.new_job_group = QGroupBox("Yeni iş")
        self.new_job_group.setAccessibleName("Yeni iş oluşturma bölümü")
        new_job_layout = QVBoxLayout(self.new_job_group)
        new_job_layout.setSpacing(10)
        self.type_label = QLabel("Ne işlemek istiyorsunuz?")
        self.type_label.setObjectName("sectionLabel")
        new_job_layout.addWidget(self.type_label)
        type_cards = QHBoxLayout()
        self.video_card = QPushButton("▶  Video\nBağlantı veya yerel dosya")
        self.document_card = QPushButton("▤  Belge\nPDF, DOCX, MD veya TXT")
        for card, accessible_name in (
            (self.video_card, "Video işi seç"),
            (self.document_card, "Belge işi seç"),
        ):
            card.setCheckable(True)
            card.setAutoExclusive(True)
            card.setProperty("kindCard", True)
            card.setMinimumHeight(62)
            card.setAccessibleName(accessible_name)
            type_cards.addWidget(card)
        self.video_card.clicked.connect(
            lambda checked: self._select_kind_card("video", checked)
        )
        self.document_card.clicked.connect(
            lambda checked: self._select_kind_card("document", checked)
        )
        new_job_layout.addLayout(type_cards)
        self.kind_hint = QLabel("Kart seçmezseniz kaynak türü otomatik algılanır.")
        self.kind_hint.setObjectName("hintLabel")
        self.kind_hint.setWordWrap(True)
        new_job_layout.addWidget(self.kind_hint)

        self.source_label = QLabel("Bağlantı veya dosya")
        self.source_label.setObjectName("sectionLabel")
        new_job_layout.addWidget(self.source_label)
        self.source.setPlaceholderText(
            "Bağlantıyı yapıştırın veya dosyayı buraya sürükleyin"
        )
        self.source.setAccessibleName("İşlenecek bağlantı veya dosya")
        self.source.setAccessibleDescription(
            "YouTube, X, Udemy bağlantısı ya da desteklenen yerel video ve belge dosyası"
        )
        browse.setAccessibleName("Bilgisayardan dosya seç")
        self.browse_button = browse
        source_grid = QGridLayout()
        source_grid.addWidget(self.source, 0, 0)
        source_grid.addWidget(browse, 0, 1)
        new_job_layout.addLayout(source_grid)

        options_form = QFormLayout()
        self.options_form = options_form
        self.language.setAccessibleName("Kaynak dil")
        options_form.addRow("Kaynak dil", self.language)
        options_form.addRow(self.document_glossary_title, self.document_glossary_widget)
        options_form.addRow(
            self.document_summary_profile_title, self.document_summary_profile
        )
        options_form.addRow(
            self.document_summary_source_title, self.document_summary_source
        )
        options_form.addRow(self.document_summary_pdf_title, self.document_summary_pdf)
        new_job_layout.addLayout(options_form)

        self.advanced_session_toggle = QPushButton("Gelişmiş X / Udemy seçenekleri")
        self.advanced_session_toggle.setCheckable(True)
        self.advanced_session_toggle.setAccessibleDescription(
            "Korumalı X veya erişim hakkınız olan tek Udemy dersi için ayrı oturum seçenekleri"
        )
        self.advanced_session_panel = QWidget()
        advanced_layout = QVBoxLayout(self.advanced_session_panel)
        advanced_layout.setContentsMargins(8, 4, 8, 4)
        advanced_row = QHBoxLayout()
        advanced_row.addWidget(self.use_browser_session)
        advanced_row.addWidget(self.browser_kind)
        advanced_row.addWidget(self.select_profile_button)
        advanced_layout.addLayout(advanced_row)
        advanced_layout.addWidget(self.browser_profile_label)
        advanced_layout.addWidget(self.udemy_access_confirmed)
        self.advanced_session_panel.setVisible(False)
        self.advanced_session_toggle.toggled.connect(
            self.advanced_session_panel.setVisible
        )
        new_job_layout.addWidget(self.advanced_session_toggle)
        new_job_layout.addWidget(self.advanced_session_panel)

        self.output_group = QGroupBox("İstediğiniz sonuçlar")
        output_layout = QGridLayout(self.output_group)
        output_layout.addWidget(self.download_only, 0, 0)
        output_layout.addWidget(self.want_subtitle, 0, 1)
        output_layout.addWidget(self.want_summary, 1, 0)
        output_layout.addWidget(self.want_dub, 1, 1)
        new_job_layout.addWidget(self.output_group)
        start_actions = QHBoxLayout()
        self.start_button.setText("İncele ve Başlat")
        self.start_button.setObjectName("primaryButton")
        self.start_button.setAccessibleDescription(
            "Kaynağı ve gereken alanı gösterir; onaydan sonra işlemi başlatır"
        )
        self.start_button.setAccessibleName("Kaynağı incele ve işi başlat")
        start_actions.addWidget(self.start_button, 1)
        new_job_layout.addLayout(start_actions)
        new_job_layout.addStretch(1)
        self.new_job_tab = QWidget()
        new_job_tab_layout = QVBoxLayout(self.new_job_tab)
        new_job_tab_layout.setContentsMargins(14, 14, 14, 14)
        new_job_tab_layout.addWidget(self.new_job_group)
        new_job_tab_layout.addStretch(1)

        self.progress_group = QGroupBox("İşlem ve sonuç")
        self.progress_group.setAccessibleName("İşlem ilerlemesi ve sonuçlar bölümü")
        progress_layout = QVBoxLayout(self.progress_group)
        self.progress.setAccessibleName("İşlem ilerlemesi")
        self.progress.setTextVisible(True)
        self.status.setObjectName("statusLabel")
        self.status.setWordWrap(True)
        self.status.setAccessibleName("İşlem durumu")
        progress_layout.addWidget(self.status)
        self.job_context_label = QLabel(
            "Henüz iş seçilmedi • Dil: — • Çıktı: — • Depolama: harici SSD"
        )
        self.job_context_label.setObjectName("hintLabel")
        self.job_context_label.setWordWrap(True)
        self.job_context_label.setAccessibleName("Seçili işin türü, dili ve depolama konumu")
        progress_layout.addWidget(self.job_context_label)
        progress_layout.addWidget(self.progress)
        progress_layout.addWidget(self.cancel_button)
        self.log.setAccessibleName("İşlem ayrıntıları")
        self.log.setMinimumHeight(190)
        progress_layout.addWidget(self.log, 1)

        self.result_label = QLabel("Hazır sonuçlar")
        self.result_label.setObjectName("sectionLabel")
        progress_layout.addWidget(self.result_label)
        result_actions = QGridLayout()
        result_actions.addWidget(self.review_button, 0, 0)
        result_actions.addWidget(self.summary_review_button, 0, 1)
        result_actions.addWidget(self.open_dub_button, 1, 0, 1, 2)
        progress_layout.addLayout(result_actions)

        self.copy_group = QGroupBox("Mac'e kopyala")
        copy_layout = QGridLayout(self.copy_group)
        self.copy_content_label = QLabel("İçerik")
        copy_layout.addWidget(self.copy_content_label, 0, 0)
        copy_layout.addWidget(self.export_kind, 0, 1, 1, 2)
        self.copy_target_label = QLabel("Hedef")
        copy_layout.addWidget(self.copy_target_label, 1, 0)
        copy_layout.addWidget(self.export_target, 1, 1)
        copy_layout.addWidget(self.export_directory_button, 1, 2)
        copy_layout.addWidget(self.export_button, 2, 0, 1, 3)
        progress_layout.addWidget(self.copy_group)
        self.processing_tab = QWidget()
        processing_tab_layout = QVBoxLayout(self.processing_tab)
        processing_tab_layout.setContentsMargins(14, 14, 14, 14)
        processing_tab_layout.addWidget(self.progress_group)

        self.history_group = QGroupBox("İş geçmişi")
        history_layout = QVBoxLayout(self.history_group)
        history_filters = QHBoxLayout()
        self.history_search = QLineEdit()
        self.history_search.setClearButtonEnabled(True)
        self.history_search.textChanged.connect(self._refresh_history)
        self.history_filter = QComboBox()
        for label, value in (
            ("Tüm işler", "all"),
            ("Videolar", "video"),
            ("Belgeler", "document"),
            ("Medya araçları", "media"),
            ("Görseller", "image"),
            ("Tamamlananlar", "completed"),
            ("İlgi gerekenler", "attention"),
        ):
            self.history_filter.addItem(label, value)
        self.history_filter.currentIndexChanged.connect(self._refresh_history)
        self.history_count_label = QLabel()
        history_filters.addWidget(self.history_search, 1)
        history_filters.addWidget(self.history_filter)
        history_filters.addWidget(self.history_count_label)
        history_layout.addLayout(history_filters)
        self.history.setAccessibleName("Geçmiş işler")
        self.history.setMinimumHeight(120)
        self.history.setMaximumHeight(180)
        self.history.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.history.customContextMenuRequested.connect(self._show_history_context_menu)
        self.history_copy_shortcut = QShortcut(QKeySequence.StandardKey.Copy, self.history)
        self.history_copy_shortcut.activated.connect(self._copy_selected_history_cell)
        history_layout.addWidget(self.history)
        compact_history_actions = QGridLayout()
        compact_history_actions.addWidget(self.retry_button, 0, 0)
        compact_history_actions.addWidget(self.add_outputs_button, 0, 1)
        compact_history_actions.addWidget(self.cleanup_button, 1, 0)
        compact_history_actions.addWidget(self.system_status_button, 1, 1)
        self.open_output_button = QPushButton("Çıktı Klasörünü Aç")
        self.open_output_button.clicked.connect(self._open_selected_output_folder)
        self.open_final_video_button = QPushButton("Final Videoyu Aç")
        self.open_final_video_button.clicked.connect(self._open_selected_final_video)
        self.open_last_export_button = QPushButton("Son Dışa Aktarma Klasörünü Aç")
        self.open_last_export_button.clicked.connect(self._open_last_export_folder)
        compact_history_actions.addWidget(self.open_output_button, 2, 0)
        compact_history_actions.addWidget(self.open_final_video_button, 2, 1)
        compact_history_actions.addWidget(self.open_last_export_button, 3, 0, 1, 2)
        history_layout.addLayout(compact_history_actions)
        self.history_tab = QWidget()
        history_tab_layout = QVBoxLayout(self.history_tab)
        history_tab_layout.setContentsMargins(14, 14, 14, 14)
        history_tab_layout.addWidget(self.history_group)
        history_tab_layout.addStretch(1)

        self.external_review_toggle = QPushButton("İsteğe bağlı harici inceleme")
        self.external_review_toggle.setCheckable(True)
        self.external_review_toggle.setAccessibleDescription(
            "Temel yerel akış için gerekli değildir ve internet bağlantısı başlatmaz"
        )
        self.external_review_panel = QFrame()
        external_layout = QGridLayout(self.external_review_panel)
        external_layout.addWidget(self.external_review_label, 0, 0, 1, 3)
        external_layout.addWidget(self.create_codex_package_button, 1, 0)
        external_layout.addWidget(self.import_codex_package_button, 1, 1)
        external_layout.addWidget(self.undo_codex_review_button, 1, 2)
        self.external_review_panel.setVisible(False)
        self.external_review_toggle.toggled.connect(
            self.external_review_panel.setVisible
        )
        self.system_tab = QWidget()
        system_layout = QVBoxLayout(self.system_tab)
        system_layout.setContentsMargins(14, 14, 14, 14)
        self.system_heading = QLabel("Sistem ve depolama")
        self.system_heading.setObjectName("pageHeading")
        self.system_intro = QLabel()
        self.system_intro.setWordWrap(True)
        self.system_intro.setObjectName("pageIntro")
        self.system_summary = QPlainTextEdit()
        self.system_summary.setReadOnly(True)
        self.system_summary.setMinimumHeight(260)
        self.system_summary.setAccessibleName("Sistem, depolama, araç ve model bilgileri")
        self.system_full_verify_button = QPushButton("Model Hashlerini Doğrula")
        self.system_full_verify_button.clicked.connect(
            lambda: self._show_system_status(verify_model_hashes=True, _inline=True)
        )
        system_buttons = QHBoxLayout()
        system_buttons.addWidget(self.system_status_button)
        system_buttons.addWidget(self.system_full_verify_button)
        system_buttons.addStretch(1)
        system_layout.addWidget(self.system_heading)
        system_layout.addWidget(self.system_intro)
        system_layout.addLayout(system_buttons)
        system_layout.addWidget(self.system_summary, 1)
        system_layout.addWidget(self.external_review_toggle)
        system_layout.addWidget(self.external_review_panel)

        self.help_tab = QWidget()
        help_layout = QVBoxLayout(self.help_tab)
        help_layout.setContentsMargins(20, 20, 20, 20)
        help_layout.setSpacing(14)
        self.help_heading = QLabel()
        self.help_heading.setObjectName("pageHeading")
        help_layout.addWidget(self.help_heading)
        self.help_labels: list[QLabel] = []
        for key in ("help.new_job", "help.processing", "help.history", "help.system", "help.privacy"):
            label = QLabel()
            label.setProperty("helpCard", True)
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByKeyboard | Qt.TextInteractionFlag.TextSelectableByMouse)
            label.setProperty("translationKey", key)
            help_layout.addWidget(label)
            self.help_labels.append(label)
        help_layout.addStretch(1)

        for page in (
            self.new_job_tab,
            self.processing_tab,
            self.history_tab,
            self.system_tab,
            self.help_tab,
        ):
            self.tabs.addTab(page, "")
        root_layout.addWidget(self.tabs, 1)

        for widget, accessible_name in (
            (self.download_only, "Yalnız indir"),
            (self.want_subtitle, "Türkçe altyazı veya belge çevirisi üret"),
            (self.want_summary, "Türkçe yazılı özet üret"),
            (self.want_dub, "Türkçe dublaj üret"),
            (self.review_button, "Metin veya altyazı sonucunu incele"),
            (self.summary_review_button, "Özet sonucunu incele"),
            (self.open_dub_button, "Dublajlı videoyu aç"),
            (self.export_button, "Seçili sonuçları Mac'e kopyala"),
            (self.system_status_button, "Sistem durumunu göster"),
        ):
            widget.setAccessibleName(accessible_name)

        self.setStyleSheet(
            """
            QGroupBox {
                border: 1px solid palette(midlight);
                border-radius: 12px;
                margin-top: 9px;
                padding: 12px 8px 8px 8px;
                font-weight: 600;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; }
            QLabel#sectionLabel { font-weight: 600; }
            QLabel#hintLabel { font-size: 12px; }
            QLabel#statusLabel {
                background: palette(alternate-base);
                border: 1px solid palette(midlight);
                border-radius: 8px;
                padding: 8px;
                font-weight: 600;
            }
            QLabel#localBadge {
                background: palette(alternate-base);
                border: 1px solid palette(midlight);
                border-radius: 10px;
                padding: 5px 8px;
                color: palette(highlight);
                font-weight: 700;
            }
            QPushButton[kindCard="true"] {
                text-align: left;
                padding: 9px;
                border: 1px solid palette(midlight);
                border-radius: 10px;
            }
            QPushButton[kindCard="true"]:checked {
                border: 3px solid palette(highlight);
                background: palette(highlight);
                color: palette(highlighted-text);
                font-weight: 750;
            }
            QPushButton#primaryButton {
                background: #087EA4;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 8px 14px;
                font-weight: 700;
            }
            QPushButton#primaryButton:disabled { background: palette(mid); }
            QPushButton#stopButton {
                background: palette(highlight);
                color: palette(highlighted-text);
                border: 2px solid palette(highlight);
                border-radius: 8px;
                padding: 8px 14px;
                font-weight: 700;
            }
            QPushButton#stopButton:disabled {
                background: palette(button);
                color: palette(mid);
                border: 1px solid palette(midlight);
            }
            QLineEdit, QComboBox, QPlainTextEdit, QTableWidget {
                background: palette(base);
                color: palette(text);
                border: 1px solid palette(midlight);
                border-radius: 7px;
                padding: 4px;
            }
            QProgressBar {
                background: palette(base);
                color: palette(text);
                border: 1px solid palette(midlight);
                text-align: center;
            }
            QProgressBar::chunk { background: palette(highlight); }
            QTabWidget#mainTabs::pane {
                border: 1px solid palette(midlight);
                border-radius: 12px;
                background: palette(window);
            }
            QTabBar::tab {
                min-width: 104px;
                padding: 10px 16px;
                margin-right: 4px;
                border: 1px solid palette(midlight);
                border-bottom: none;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                background: palette(button);
                color: palette(button-text);
                font-weight: 600;
            }
            QTabBar::tab:selected {
                background: palette(highlight);
                color: palette(highlighted-text);
                font-weight: 750;
            }
            QLabel#pageHeading { font-size: 22px; font-weight: 750; }
            QLabel#pageIntro { font-size: 14px; }
            QLabel[helpCard="true"] {
                background: palette(alternate-base);
                border: 1px solid palette(midlight);
                border-radius: 10px;
                padding: 14px;
                font-size: 14px;
            }
            """
        )
        self.content_page = content
        self.loading_page = QWidget()
        loading_layout = QVBoxLayout(self.loading_page)
        loading_layout.setContentsMargins(48, 48, 48, 48)
        loading_layout.addStretch(1)
        loading_logo = QLabel()
        if logo_path.is_file():
            loading_logo.setPixmap(QPixmap(str(logo_path)).scaled(96, 96, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        loading_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.loading_title = QLabel()
        self.loading_title.setObjectName("pageHeading")
        self.loading_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.loading_detail = QLabel()
        self.loading_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.loading_detail.setWordWrap(True)
        loading_progress = QProgressBar()
        loading_progress.setRange(0, 0)
        loading_progress.setMaximumWidth(420)
        loading_row = QHBoxLayout()
        loading_row.addStretch(1)
        loading_row.addWidget(loading_progress)
        loading_row.addStretch(1)
        loading_layout.addWidget(loading_logo)
        loading_layout.addWidget(self.loading_title)
        loading_layout.addWidget(self.loading_detail)
        loading_layout.addLayout(loading_row)
        loading_layout.addStretch(2)
        self.page_stack = QStackedWidget()
        self.page_stack.addWidget(self.loading_page)
        self.page_stack.addWidget(content)
        self.page_stack.setCurrentWidget(self.loading_page)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self.page_stack)
        scroll.setAccessibleName("KSI Local Studio ana içerik")
        self.setCentralWidget(scroll)
        # The hidden compatibility selector still owns the automatic/video/document
        # state. Give it a live parent even though the two cards are its visible UI.
        self.job_kind.setParent(content)
        self.job_kind.setVisible(False)
        self._sync_kind_cards()
        self.setTabOrder(self.video_card, self.document_card)
        self.setTabOrder(self.document_card, self.source)
        self.setTabOrder(self.source, browse)
        self.setTabOrder(browse, self.language)
        self.setTabOrder(self.language, self.start_button)
        self.setTabOrder(self.start_button, self.cancel_button)
        self.setTabOrder(self.cancel_button, self.history_search)
        self.setTabOrder(self.history_search, self.history_filter)
        self.setTabOrder(self.history_filter, self.history)
        self.setTabOrder(self.history, self.retry_button)
        self.setTabOrder(self.retry_button, self.open_output_button)
        self.setTabOrder(self.open_output_button, self.open_final_video_button)
        self.setTabOrder(self.open_final_video_button, self.open_last_export_button)
        from ksi_local.ui.shell import StudioShell

        self.studio_shell = StudioShell(self, root_layout, header_layout, logo_path)
        self._apply_accessible_palette()
        self._apply_ui_language()
        self._apply_theme(self.preferences.theme)
        self._refresh_history()
        self.workspace_timer = QTimer(self)
        self.workspace_timer.setInterval(5000)
        self.workspace_timer.timeout.connect(self._poll_workspace)
        # The event loop paints the window first. Removable-volume permission
        # prompts and a slow disk can therefore never hide the whole application.
        QTimer.singleShot(0, self._initialize_workspace)

    def _t(self, key: str, **values: object) -> str:
        return ui_text(key, self.preferences.ui_language, **values)

    @staticmethod
    def _format_model_bytes(value: int) -> str:
        return _human_bytes(value)

    def _actionable_message(self, message: str, action: str) -> str:
        return _actionable_message(
            message,
            action,
            action_label=self._t("error.action_label"),
        )

    @staticmethod
    def _set_combo_item_texts(combo: QComboBox, labels: dict[str, str]) -> None:
        for index in range(combo.count()):
            data = str(combo.itemData(index) or "")
            if data in labels:
                combo.setItemText(index, labels[data])

    def _ui_language_changed(self) -> None:
        selected = str(self.ui_language.currentData() or "tr")
        if selected == self.preferences.ui_language:
            return
        self.preferences = replace(self.preferences, ui_language=selected)
        save_preferences(self.preferences)
        self._apply_ui_language()
        self._refresh_history()

    def _theme_changed(self) -> None:
        selected = str(self.theme.currentData() or "system")
        if selected != self.preferences.theme:
            self.preferences = replace(self.preferences, theme=selected)
            save_preferences(self.preferences)
        self._apply_theme(selected)

    def _apply_theme(self, theme: str) -> None:
        application = QApplication.instance()
        if application is None:
            return
        if theme == "system":
            system_style = QStyleFactory.create(self._system_style_name)
            if system_style is not None:
                application.setStyle(system_style)
            palette = QPalette(self._system_palette)
        else:
            fusion = QStyleFactory.create("Fusion")
            if fusion is not None:
                application.setStyle(fusion)
            dark = theme == "dark"
            palette = QPalette()
            colors = {
                QPalette.ColorRole.Window: "#252d35" if dark else "#f4f7fa",
                QPalette.ColorRole.WindowText: "#f4f7fa" if dark else "#17212b",
                QPalette.ColorRole.Base: "#303a44" if dark else "#ffffff",
                QPalette.ColorRole.AlternateBase: "#2b3540" if dark else "#e9f0f5",
                QPalette.ColorRole.Text: "#f4f7fa" if dark else "#17212b",
                QPalette.ColorRole.Button: "#35424d" if dark else "#ffffff",
                QPalette.ColorRole.ButtonText: "#f4f7fa" if dark else "#17212b",
                QPalette.ColorRole.Midlight: "#667785" if dark else "#b8c6d1",
                QPalette.ColorRole.Mid: "#82919d" if dark else "#768794",
                QPalette.ColorRole.Highlight: "#20a7d1" if dark else "#087ea4",
                QPalette.ColorRole.HighlightedText: "#ffffff",
                QPalette.ColorRole.ToolTipBase: "#303a44" if dark else "#ffffff",
                QPalette.ColorRole.ToolTipText: "#f4f7fa" if dark else "#17212b",
            }
            for role, value in colors.items():
                palette.setColor(role, QColor(value))
        application.setPalette(palette)
        self.setPalette(palette)
        for widget in self.findChildren(QWidget):
            widget.setPalette(palette)
        style_sheet = self.styleSheet()
        if style_sheet:
            self.setStyleSheet("")
            self.setStyleSheet(style_sheet)
        self._apply_accessible_palette()

        from ksi_local.ui.theme import studio_palette, studio_stylesheet

        is_dark = palette.color(QPalette.ColorRole.Window).lightness() < 128
        palette = studio_palette(is_dark)
        application.setPalette(palette)
        self.setPalette(palette)
        for widget in self.findChildren(QWidget):
            widget.setPalette(palette)
        self.setStyleSheet(studio_stylesheet(is_dark))

    def _apply_ui_language(self) -> None:
        """Retranslate the persistent shell without rebuilding active job state."""
        language = self.preferences.ui_language
        short_version = ".".join(__version__.split(".")[:2])
        self.header_subtitle.setText(self._t("header.subtitle"))
        self.local_badge.setText(self._t("badge.local", version=short_version))
        self.ui_language_label.setText(self._t("language.ui"))
        self.theme_label.setText(self._t("theme.label"))
        self._set_combo_item_texts(
            self.theme,
            {
                "system": self._t("theme.system"),
                "light": self._t("theme.light"),
                "dark": self._t("theme.dark"),
            },
        )
        for index, key in enumerate(("tab.new_job", "tab.processing", "tab.history", "tab.system", "tab.help")):
            self.tabs.setTabText(index, self._t(key))
        self.loading_title.setText(self._t("loading.title"))
        self.loading_detail.setText(self._t("loading.detail"))
        self.system_heading.setText(self._t("system.heading"))
        self.system_intro.setText(self._t("system.intro"))
        self.system_status_button.setText(self._t("system.refresh"))
        self.system_full_verify_button.setText(self._t("system.full_verify"))
        if not self.system_summary.toPlainText().strip():
            self.system_summary.setPlainText(self._t("system.waiting"))
        self.help_heading.setText(self._t("help.heading"))
        for label in self.help_labels:
            label.setText(self._t(str(label.property("translationKey"))))
        self.new_job_group.setTitle(self._t("group.new_job"))
        self.type_label.setText(self._t("prompt.kind"))
        self.video_card.setText(self._t("card.video"))
        self.document_card.setText(self._t("card.document"))
        self.source_label.setText(self._t("label.source"))
        self.source.setPlaceholderText(self._t("placeholder.source"))
        self.browse_button.setText(self._t("button.browse"))
        language_label = self.options_form.labelForField(self.language)
        if language_label is not None:
            language_label.setText(self._t("label.source_language"))
        self._set_combo_item_texts(
            self.language,
            {
                AUTO_LANGUAGE: self._t("language.auto"),
                **{
                    code: source_language_name(code, language)
                    for code in TURKISH_SOURCE_LANGUAGE_NAMES
                },
            },
        )
        self.document_glossary_title.setText(self._t("document.glossary"))
        self.document_glossary_button.setText(self._t("button.glossary"))
        self.document_glossary_clear.setText(self._t("button.clear"))
        if self.document_glossary_path is None:
            self.document_glossary_label.setText(self._t("optional"))
        self.document_summary_profile_title.setText(self._t("summary.length"))
        self._set_combo_item_texts(
            self.document_summary_profile,
            {
                "short": self._t("summary.short"),
                "standard": self._t("summary.standard"),
                "detailed": self._t("summary.detailed"),
            },
        )
        self.document_summary_source_title.setText(self._t("summary.source"))
        self._set_combo_item_texts(
            self.document_summary_source,
            {
                "auto": self._t("summary.source_auto"),
                "source": self._t("summary.source_original"),
                "translation": self._t("summary.source_translation"),
            },
        )
        self.document_summary_pdf_title.setText(self._t("additional_output"))
        self.document_summary_pdf.setText(self._t("summary.pdf"))
        self.advanced_session_toggle.setText(self._t("advanced.sessions"))
        self.use_browser_session.setText(self._t("session.use"))
        self.select_profile_button.setText(self._t("session.choose"))
        self.browser_profile_label.setText(self._t("session.hint"))
        self.udemy_access_confirmed.setText(self._t("udemy.confirm"))
        self.output_group.setTitle(self._t("group.outputs"))
        self.download_only.setText(self._t("output.download"))
        self.want_subtitle.setText(self._t("output.subtitle"))
        self.want_summary.setText(self._t("output.summary"))
        self.want_dub.setText(self._t("output.dub"))
        self.start_button.setText(self._t("button.start"))
        self.progress_group.setTitle(self._t("group.progress"))
        if self.current_job_id is None and not self._process_is_running():
            self.status.setText(self._t("status.checking"))
            self.job_context_label.setText(self._t("context.none"))
        self.cancel_button.setText(self._t("button.stop"))
        self.log.setPlaceholderText(self._t("log.placeholder"))
        self.result_label.setText(self._t("results.ready"))
        self.review_button.setText(self._t("button.review_subtitle"))
        self.summary_review_button.setText(self._t("button.review_summary"))
        self.open_dub_button.setText(self._t("button.open_dub"))
        self.copy_group.setTitle(self._t("group.copy"))
        self.copy_content_label.setText(self._t("copy.content"))
        self.copy_target_label.setText(self._t("copy.target"))
        self._set_combo_item_texts(
            self.export_kind,
            {
                "all": self._t("export.all"), "video": self._t("export.video"),
                "summary": self._t("export.summary"),
                "subtitle": self._t("export.subtitle"),
                "translation": self._t("export.translation"),
            },
        )
        self._set_combo_item_texts(
            self.export_target,
            {
                "desktop": self._t("target.desktop"),
                "documents": self._t("target.documents"),
                "custom": self._t("target.custom"),
            },
        )
        self.export_directory_button.setText(self._t("button.choose_folder"))
        self.export_button.setText(self._t("button.export"))
        self.history_group.setTitle(self._t("group.history"))
        self.history_search.setPlaceholderText(self._t("history.search_placeholder"))
        self.history_search.setAccessibleName(self._t("history.accessible_search"))
        self.history_filter.setAccessibleName(self._t("history.accessible_filter"))
        self.history.setAccessibleName(self._t("history.accessible_table"))
        self._set_combo_item_texts(
            self.history_filter,
            {
                "all": self._t("history.filter_all"),
                "video": self._t("history.filter_video"),
                "document": self._t("history.filter_document"),
                "completed": self._t("history.filter_completed"),
                "attention": self._t("history.filter_attention"),
            },
        )
        self.history.setHorizontalHeaderLabels(
            [self._t(key) for key in (
                "history.job", "history.source", "history.kind_language",
                "history.outputs", "history.status", "history.updated",
            )]
        )
        self.retry_button.setText(self._t("button.retry"))
        self.add_outputs_button.setText(self._t("button.add_outputs"))
        self.cleanup_button.setText(self._t("button.cleanup"))
        self.system_status_button.setText(self._t("system.refresh"))
        self.open_output_button.setText(self._t("history.open_output"))
        self.open_final_video_button.setText(self._t("history.open_final_video"))
        self.open_last_export_button.setText(self._t("history.open_last_export"))
        for button in (
            self.open_output_button,
            self.open_final_video_button,
            self.open_last_export_button,
        ):
            button.setAccessibleDescription(self._t("history.accessible_actions"))
        self.external_review_toggle.setText(self._t("external.toggle"))
        self.external_review_label.setText(self._t("external.label"))
        self.create_codex_package_button.setText(self._t("external.create"))
        self.import_codex_package_button.setText(self._t("external.import"))
        self.undo_codex_review_button.setText(self._t("external.undo"))
        for widget in (
            self.local_badge,
            self.new_job_group,
            self.video_card,
            self.document_card,
            self.source,
            self.browse_button,
            self.language,
            self.start_button,
            self.progress_group,
            self.progress,
            self.status,
            self.job_context_label,
            self.log,
            self.download_only,
            self.want_subtitle,
            self.want_summary,
            self.want_dub,
            self.review_button,
            self.summary_review_button,
            self.open_dub_button,
            self.export_button,
            self.system_status_button,
        ):
            visible = widget.text() if hasattr(widget, "text") else ""
            if not visible and hasattr(widget, "title"):
                visible = widget.title()
            if visible:
                widget.setAccessibleName(str(visible).replace("\n", " "))
        self.language.setAccessibleName(self._t("label.source_language"))
        self.progress.setAccessibleName(self._t("group.progress"))
        self.status.setAccessibleName(self._t("group.progress"))
        self.job_context_label.setAccessibleName(self._t("context.none"))
        self.log.setAccessibleName(self._t("log.placeholder"))
        self._sync_kind_cards()
        self._source_changed(self.source.text())
        if hasattr(self, "studio_shell"):
            self.studio_shell.retranslate()

    def _apply_accessible_palette(self) -> None:
        """Keep placeholder text readable across live macOS theme changes."""
        foreground = self.palette().color(QPalette.ColorRole.Mid)
        for widget in (self.source, self.log):
            palette = widget.palette()
            palette.setColor(QPalette.ColorRole.PlaceholderText, foreground)
            widget.setPalette(palette)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() in {
            QEvent.Type.ApplicationPaletteChange,
            QEvent.Type.PaletteChange,
        } and hasattr(self, "source"):
            self._apply_accessible_palette()

    def resizeEvent(self, event: object) -> None:
        super().resizeEvent(event)
        if getattr(self, "main_columns", None) is not None:
            direction = (
                QBoxLayout.Direction.TopToBottom
                if self.width() < 900
                else QBoxLayout.Direction.LeftToRight
            )
            self.main_columns.setDirection(direction)

    def _initialize_workspace(self) -> None:
        if self.workspace_initialized or self.closing:
            return
        self.workspace_initialized = True
        # Capture only jobs that were actively running when the previous app
        # process disappeared. Ordinary queued jobs must remain under the
        # user's control, while interrupted work should continue from its
        # checkpoint after the SSD has been verified.
        self.startup_interrupted_job_ids = tuple(
            record.id
            for record in self.store.list_jobs()
            if record.status is JobStatus.RUNNING
        )
        self.workspace_timer.start()
        self._begin_workspace_resolution(recover_interrupted=True)

    def _load_workspace(self) -> None:
        self.status.setText(self._t("runtime.workspace_rechecking"))
        self._begin_workspace_resolution(recover_interrupted=False)

    def _begin_workspace_resolution(self, *, recover_interrupted: bool) -> None:
        if self.workspace_resolution_pending:
            return
        self.workspace_resolution_pending = True
        self.workspace_thread = threading.Thread(
            target=self._resolve_workspace_in_background,
            args=(recover_interrupted,),
            name="KSI Local Studio-SSD-Denetimi",
            daemon=True,
        )
        self.workspace_thread.start()

    def _resolve_workspace_in_background(self, recover_interrupted: bool) -> None:
        try:
            resolved = resolve_workspace(initialize=True)
        except (OSError, RuntimeError, ValueError) as error:
            resolved = None
            resolution_error: RuntimeError | None = RuntimeError(
                redact_sensitive_text(str(error))
            )
        else:
            resolution_error = None
        try:
            self.workspace_bridge.finished.emit(
                resolved,
                resolution_error,
                recover_interrupted,
            )
        except RuntimeError:
            # The window may have closed while the removable disk was blocked.
            return

    def _workspace_resolution_finished(
        self,
        resolved: WorkspacePaths | None,
        error: RuntimeError | None,
        recover_interrupted: bool,
    ) -> None:
        if self.closing:
            return
        if hasattr(self, "page_stack"):
            self.page_stack.setCurrentWidget(self.content_page)
        self.workspace_resolution_pending = False
        if resolved is None:
            had_workspace = self.workspace is not None
            self.workspace_poll_failures += 1
            workspace_path_available = bool(
                had_workspace
                and self.workspace is not None
                and self.workspace.root.is_dir()
                and os.access(self.workspace.root, os.W_OK)
            )
            if (
                had_workspace
                and not recover_interrupted
                and workspace_path_available
                and self._process_is_running()
            ):
                self.status.setText(
                    self._t("runtime.identity_delayed")
                )
                return
            if (
                had_workspace
                and not recover_interrupted
                and workspace_path_available
                and self.workspace_poll_failures < 3
            ):
                self.status.setText(self._t("runtime.response_delayed"))
                return
            self.waiting_for_ssd = had_workspace and self._process_is_running()
            self.workspace = None
            self.core.set_workspace(None)
            self.start_button.setEnabled(
                self._source_is_image() and not self._process_is_running()
            )
            self.export_button.setEnabled(False)
            self.cleanup_button.setEnabled(False)
            self.review_button.setEnabled(False)
            self.summary_review_button.setEnabled(False)
            self.retry_button.setEnabled(False)
            self.add_outputs_button.setEnabled(False)
            if had_workspace:
                self.status.setText(
                    self._actionable_message(
                        self._t("runtime.disconnected"),
                        self._t("runtime.disconnected_action"),
                    )
                )
            else:
                self.status.setText(
                    self._actionable_message(
                        self._t("runtime.unavailable"),
                        self._t("runtime.unavailable_action"),
                    )
                )
            if recover_interrupted or had_workspace:
                self.store.recover_interrupted_jobs(workspace_available=False)
            self._refresh_history()
            if self._process_is_running():
                self.pending.clear()
                assert self.process is not None
                process = self.process
                self._terminate_process(process)
                QTimer.singleShot(3000, lambda: self._kill_if_running(process))
            return

        self.workspace_poll_failures = 0
        was_missing = self.workspace is None
        self.workspace = resolved
        self.core.set_workspace(resolved)
        if recover_interrupted or was_missing:
            self.model_controller.refresh()
        if recover_interrupted:
            recovered = self.store.recover_interrupted_jobs(workspace_available=True)
            if recovered:
                self.status.setText(self._t("runtime.resuming_count", count=recovered))
            else:
                self.status.setText(self._t("runtime.ready", path=resolved.root))
        elif was_missing:
            resumed = self.store.resume_waiting_jobs()
            suffix = self._t("runtime.retry_ready_count", count=resumed) if resumed else ""
            self.status.setText(self._t("runtime.reconnected") + suffix)
        if not self._process_is_running() and not self.preflight_pending:
            self.start_button.setEnabled(True)
        self._refresh_history()
        self._schedule_interrupted_job_resume()
        if self.first_run_pending:
            self.first_run_pending = False
            self.preferences = replace(
                self.preferences, onboarding_version=ONBOARDING_VERSION
            )
            try:
                save_preferences(self.preferences)
            except OSError:
                pass

    def _schedule_interrupted_job_resume(self) -> None:
        if (
            not self.startup_interrupted_job_ids
            or self.workspace is None
            or self._process_is_running()
            or self.preflight_pending
            or self.maintenance_pending
        ):
            return
        job_id = self.startup_interrupted_job_ids[0]
        self.startup_interrupted_job_ids = self.startup_interrupted_job_ids[1:]
        QTimer.singleShot(0, lambda: self._resume_interrupted_job(job_id))

    def _resume_interrupted_job(self, job_id: str) -> None:
        if self.closing or self.workspace is None or self._process_is_running():
            return
        try:
            record = self.store.get_job(job_id)
        except KeyError:
            return
        if record.status is not JobStatus.QUEUED:
            return
        if record.job_kind in {JobKind.MEDIA, JobKind.IMAGE}:
            self.tool_controller.resume(record.id)
            return
        try:
            platform = (
                validate_source_url(record.source_reference).platform
                if record.source_reference.casefold().startswith("https://")
                else None
            )
        except SourceURLValidationError:
            platform = None
        if platform is Platform.UDEMY:
            self.status.setText(
                self._t("runtime.udemy_resume")
            )
            return
        self.status.setText(self._t("runtime.resuming"))
        self._start(existing_job=record)

    def _process_is_running(self) -> bool:
        return bool(
            (getattr(self, "tool_controller", None) is not None and self.tool_controller.busy)
            or (self.process is not None
            and self.process.state() != QProcess.ProcessState.NotRunning)
        )

    def _poll_workspace(self) -> None:
        self._begin_workspace_resolution(recover_interrupted=False)

    @staticmethod
    def _kill_if_running(process: QProcess) -> None:
        try:
            if process.state() != QProcess.ProcessState.NotRunning:
                process_id = int(process.processId())
                if process_id > 0 and os.getpgid(process_id) == process_id:
                    os.killpg(process_id, signal.SIGKILL)
                else:
                    process.kill()
        except RuntimeError:
            # Qt may already have deleted a process that exited before this timer.
            pass
        except OSError:
            process.kill()

    @staticmethod
    def _terminate_process(process: QProcess) -> None:
        try:
            process_id = int(process.processId())
            if process_id > 0 and os.getpgid(process_id) == process_id:
                os.killpg(process_id, signal.SIGTERM)
                return
        except OSError:
            pass
        process.terminate()

    def _job_kind_language_label(self, record: JobRecord) -> str:
        if record.job_kind in {JobKind.MEDIA, JobKind.IMAGE}:
            from ksi_local.ui.strings import text
            return text("images" if record.job_kind is JobKind.IMAGE else "video", self.preferences.ui_language)
        kind = self._jt("document" if record.job_kind is JobKind.DOCUMENT else "video")
        language = (
            self._jt("automatic")
            if record.source_language == AUTO_LANGUAGE
            else source_language_name(record.source_language, self.preferences.ui_language)
        )
        return f"{kind} · {language}"

    def _job_output_label(self, record: JobRecord) -> str:
        if record.job_kind in {JobKind.MEDIA, JobKind.IMAGE}:
            from ksi_local.ui.strings import text
            return text("images" if record.job_kind is JobKind.IMAGE else "video", self.preferences.ui_language)
        if record.download_only:
            return self._jt("download")
        outputs: list[str] = []
        if record.want_subtitle:
            outputs.append(
                self._jt("translation" if record.job_kind is JobKind.DOCUMENT else "subtitle")
            )
        if record.want_summary:
            outputs.append(self._jt("summary"))
        if record.want_dub:
            outputs.append(self._jt("dub"))
        return ", ".join(outputs) or "—"

    def _jt(self, key: str) -> str:
        return job_term(key, self.preferences.ui_language)

    def _job_status_label(self, status: JobStatus) -> str:
        return self._jt(status.value)

    @staticmethod
    def _safe_history_source(record: JobRecord) -> str:
        """Return a copy/open value that cannot contain URL credentials or query secrets."""
        try:
            _kind, safe = safe_source_reference(record.source_reference)
        except (OSError, ValueError):
            return ""
        return safe

    def _copy_history_text(self, value: str) -> None:
        safe = str(value).strip()
        if not safe:
            return
        QApplication.clipboard().setText(safe)
        self.status.setText(self._t("history.copied"))

    def _copy_selected_source(self) -> None:
        record = self._selected_record()
        if record is not None:
            self._copy_history_text(self._safe_history_source(record))

    def _copy_selected_job_id(self) -> None:
        record = self._selected_record()
        if record is not None:
            self._copy_history_text(record.id)

    def _copy_selected_history_cell(self) -> None:
        record = self._selected_record()
        item = self.history.currentItem()
        if record is None or item is None:
            return
        if item.column() == 0:
            self._copy_selected_job_id()
        elif item.column() == 1:
            self._copy_selected_source()
        else:
            self._copy_history_text(item.text())

    def _open_url_or_path(self, value: str, *, local: bool) -> bool:
        if not value:
            return False
        url = QUrl.fromLocalFile(value) if local else QUrl(value)
        return bool(QDesktopServices.openUrl(url))

    def _open_selected_source(self) -> None:
        record = self._selected_record()
        if record is None:
            return
        source = self._safe_history_source(record)
        local = record.source_kind == "file"
        if local and not Path(source).is_file():
            opened = False
        else:
            opened = self._open_url_or_path(source, local=local)
        if not opened:
            QMessageBox.warning(
                self, self._t("history.open_failed_title"), self._t("history.open_failed")
            )

    def _selected_output_directory(self) -> Path | None:
        record = self._selected_record()
        if record is None:
            return None
        job = Path(record.job_directory)
        output = job / "outputs"
        if (
            not self._job_directory_is_valid(job)
            or not output.is_dir()
            or output.is_symlink()
        ):
            return None
        return output

    def _selected_final_video(self) -> Path | None:
        record = self._selected_record()
        output = self._selected_output_directory()
        if record is None or output is None:
            return None
        for name in (
            "turkce-dublaj.mp4",
            "turkce-altyazili.mp4",
            "turkce-altyazili.mkv",
        ):
            candidate = output / name
            if candidate.is_file() and not candidate.is_symlink():
                return candidate
        source = Path(record.job_directory) / "source"
        if source.is_dir() and not source.is_symlink():
            return next(
                (
                    candidate
                    for candidate in sorted(source.iterdir())
                    if candidate.is_file()
                    and not candidate.is_symlink()
                    and candidate.suffix.casefold() in {".mp4", ".mkv", ".mov", ".webm"}
                    and not candidate.name.endswith((".part", ".partial"))
                ),
                None,
            )
        return None

    def _open_selected_output_folder(self) -> None:
        output = self._selected_output_directory()
        if output is None:
            QMessageBox.information(
                self, self._t("history.no_output_title"), self._t("history.no_output")
            )
            return
        if not self._open_url_or_path(str(output), local=True):
            QMessageBox.warning(
                self, self._t("history.open_failed_title"), self._t("history.open_failed")
            )

    def _open_selected_final_video(self) -> None:
        video = self._selected_final_video()
        if video is None:
            QMessageBox.information(
                self, self._t("history.no_video_title"), self._t("history.no_video")
            )
            return
        if not self._open_url_or_path(str(video), local=True):
            QMessageBox.warning(
                self, self._t("history.open_failed_title"), self._t("history.open_failed")
            )

    def _open_last_export_folder(self) -> None:
        raw = self.preferences.last_export_directory
        folder = Path(raw).expanduser() if raw else None
        if folder is None or not folder.is_dir() or folder.is_symlink():
            QMessageBox.information(
                self, self._t("history.no_export_title"), self._t("history.no_export")
            )
            return
        if not self._open_url_or_path(str(folder.resolve()), local=True):
            QMessageBox.warning(
                self, self._t("history.open_failed_title"), self._t("history.open_failed")
            )

    def _show_history_context_menu(self, position: object) -> None:
        index = self.history.indexAt(position)
        if index.isValid():
            self.history.selectRow(index.row())
            self.history.setCurrentCell(index.row(), index.column())
        menu = self._history_context_menu()
        if menu is not None:
            menu.exec(self.history.viewport().mapToGlobal(position))

    def _history_context_menu(self) -> QMenu | None:
        record = self._selected_record()
        if record is None:
            return None
        menu = QMenu(self.history)
        copy_source = QAction(
            self._t("history.copy_path" if record.source_kind == "file" else "history.copy_link"),
            menu,
        )
        copy_source.triggered.connect(self._copy_selected_source)
        menu.addAction(copy_source)
        open_source = QAction(self._t("history.open_source"), menu)
        open_source.triggered.connect(self._open_selected_source)
        menu.addAction(open_source)
        copy_id = QAction(self._t("history.copy_id"), menu)
        copy_id.triggered.connect(self._copy_selected_job_id)
        menu.addAction(copy_id)
        menu.addSeparator()
        open_output = QAction(self._t("history.open_output"), menu)
        open_output.setEnabled(self._selected_output_directory() is not None)
        open_output.triggered.connect(self._open_selected_output_folder)
        menu.addAction(open_output)
        open_video = QAction(self._t("history.open_final_video"), menu)
        open_video.setEnabled(self._selected_final_video() is not None)
        open_video.triggered.connect(self._open_selected_final_video)
        menu.addAction(open_video)
        open_export = QAction(self._t("history.open_last_export"), menu)
        export_path = self.preferences.last_export_directory
        open_export.setEnabled(bool(export_path and Path(export_path).is_dir()))
        open_export.triggered.connect(self._open_last_export_folder)
        menu.addAction(open_export)
        return menu

    def _update_job_context(self, record: JobRecord | None) -> None:
        if record is None:
            self.job_context_label.setText(self._t("context.none"))
            return
        kind = self._jt("document" if record.job_kind is JobKind.DOCUMENT else "video")
        language = (
            self._jt("detecting")
            if record.source_language == AUTO_LANGUAGE
            else source_language_name(record.source_language, self.preferences.ui_language)
        )
        storage = Path(record.job_directory).anchor.rstrip("/") or self._jt("external_ssd")
        if "/Volumes/" in record.job_directory:
            parts = Path(record.job_directory).parts
            storage = f"SSD: {parts[2]}" if len(parts) > 2 else "SSD"
        self.job_context_label.setText(
            f"{kind} • {self._jt('language')}: {language} • "
            f"{self._jt('output')}: {self._job_output_label(record)} • "
            f"{self._jt('storage')}: {storage}"
        )

    def _refresh_history(self) -> None:
        selected_id = self._selected_job_id() or self.current_job_id
        all_records = self.store.list_jobs(limit=500)
        query = self.history_search.text().casefold().strip()
        selected_filter = str(self.history_filter.currentData() or "all")

        def matches(record: JobRecord) -> bool:
            if selected_filter in {"media", "image"} and record.job_kind.value != selected_filter:
                return False
            if selected_filter == "video" and record.job_kind is not JobKind.VIDEO:
                return False
            if selected_filter == "document" and record.job_kind is not JobKind.DOCUMENT:
                return False
            if selected_filter == "completed" and record.status is not JobStatus.COMPLETED:
                return False
            if selected_filter == "attention" and record.status not in {
                JobStatus.WAITING_FOR_SSD,
                JobStatus.PAUSED,
                JobStatus.CANCELLED,
                JobStatus.FAILED,
            }:
                return False
            if not query:
                return True
            safe_source = self._safe_history_source(record)
            searchable = " ".join(
                (
                    record.id,
                    Path(safe_source).name if record.source_kind == "file" else safe_source,
                    self._job_kind_language_label(record),
                    self._job_output_label(record),
                    self._job_status_label(record.status),
                )
            ).casefold()
            return query in searchable

        records = [record for record in all_records if matches(record)]
        self.history_count_label.setText(
            self._t("history.count", shown=len(records), total=len(all_records))
        )
        self.history.setRowCount(len(records))
        row_to_select: int | None = None
        for row, record in enumerate(records):
            source = self._safe_history_source(record)
            source_label = Path(source).name if record.source_kind == "file" else source
            values = (
                record.id[:8],
                source_label,
                self._job_kind_language_label(record),
                self._job_output_label(record),
                self._job_status_label(record.status),
                record.updated_at.replace("T", " ")[:16],
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, record.id)
                if column == 1:
                    item.setToolTip(source)
                    item.setData(Qt.ItemDataRole.UserRole, record.id)
                if column in {2, 3}:
                    item.setToolTip(
                        f"{self._job_kind_language_label(record)} · "
                        f"{self._job_output_label(record)}"
                    )
                self.history.setItem(row, column, item)
            if record.id == selected_id:
                row_to_select = row
        if row_to_select is None and records:
            row_to_select = 0
        if row_to_select is not None:
            self.history.selectRow(row_to_select)
        self._history_selection_changed()

    def _selected_job_id(self) -> str | None:
        selected = self.history.selectionModel().selectedRows()
        if not selected:
            return None
        item = self.history.item(selected[0].row(), 0)
        if item is None:
            return None
        value = item.data(Qt.ItemDataRole.UserRole)
        return str(value) if value else None

    def _selected_record(self) -> JobRecord | None:
        job_id = self._selected_job_id()
        if not job_id:
            return None
        try:
            return self.store.get_job(job_id)
        except KeyError:
            return None

    def _history_selection_changed(self) -> None:
        record = self._selected_record()
        if record and record.job_kind in {JobKind.MEDIA, JobKind.IMAGE}:
            self.export_kind.setCurrentIndex(self.export_kind.findData("all"))
        self._update_job_context(record)
        idle = not self._process_is_running() and not self.maintenance_pending
        has_document_translation = bool(
            record
            and record.job_kind is JobKind.DOCUMENT
            and (Path(record.job_directory) / "outputs/belge-turkce.jsonl").is_file()
            and not (Path(record.job_directory) / "outputs/belge-turkce.jsonl").is_symlink()
            and (Path(record.job_directory) / "outputs/belge-turkce.kalite.json").is_file()
            and not (Path(record.job_directory) / "outputs/belge-turkce.kalite.json").is_symlink()
        )
        self.review_button.setText(
            self._t("button.review_document_translation")
            if has_document_translation
            else self._t("button.open_document_text")
            if record and record.job_kind is JobKind.DOCUMENT
            else self._t("button.review_subtitle")
        )
        retryable = {
            JobStatus.QUEUED,
            JobStatus.WAITING_FOR_SSD,
            JobStatus.PAUSED,
            JobStatus.CANCELLED,
            JobStatus.FAILED,
            JobStatus.IMPORTED,
        }
        extraction_missing = bool(
            record
            and record.job_kind is JobKind.DOCUMENT
            and record.status is JobStatus.EXTRACTED
            and not all(
                (Path(record.job_directory) / relative).is_file()
                and not (Path(record.job_directory) / relative).is_symlink()
                for relative in (
                    "work/belge-kaynagi.jsonl",
                    "work/belge-kaynagi.txt",
                    "work/belge-kaynagi.kalite.json",
                )
            )
        )
        translation_missing = bool(
            record
            and record.job_kind is JobKind.DOCUMENT
            and record.want_subtitle
            and record.status in {JobStatus.TRANSLATED, JobStatus.SUMMARIZED}
            and not all(
                (Path(record.job_directory) / relative).is_file()
                and not (Path(record.job_directory) / relative).is_symlink()
                for relative in (
                    "outputs/belge-turkce.jsonl",
                    "outputs/belge-turkce.txt",
                    "outputs/belge-turkce.md",
                    "outputs/belge-turkce.docx",
                    "outputs/belge-turkce.pdf",
                    "outputs/belge-turkce.kalite.json",
                )
            )
        )
        translation_pending = bool(
            record
            and record.job_kind is JobKind.DOCUMENT
            and record.status in {JobStatus.EXTRACTED, JobStatus.SUMMARIZED}
            and record.want_subtitle
            and not has_document_translation
        )
        summary_path = (
            Path(record.job_directory) / "outputs/belge-ozeti.md" if record else None
        )
        has_document_summary = bool(
            record
            and record.job_kind is JobKind.DOCUMENT
            and summary_path
            and summary_path.is_file()
            and not summary_path.is_symlink()
            and (Path(record.job_directory) / "outputs/belge-ozeti.kalite.json").is_file()
        )
        summary_missing = bool(
            record
            and record.job_kind is JobKind.DOCUMENT
            and record.status is JobStatus.SUMMARIZED
            and record.want_summary
            and not has_document_summary
        )
        summary_pending = bool(
            record
            and record.job_kind is JobKind.DOCUMENT
            and record.status in {JobStatus.EXTRACTED, JobStatus.TRANSLATED}
            and record.want_summary
            and not has_document_summary
        )
        self.retry_button.setText(
            self._t("button.create_document_summary")
            if summary_pending and not translation_pending
            else self._t("button.create_missing_document_outputs")
            if summary_pending and translation_pending
            else self._t("button.translate_document")
            if translation_pending
            else self._t("button.retry")
        )
        self.retry_button.setEnabled(
            bool(
                record
                and (
                    record.status in retryable
                    or extraction_missing
                    or translation_missing
                    or translation_pending
                    or summary_missing
                    or summary_pending
                )
                and idle
                and self.workspace
            )
        )
        self.export_button.setEnabled(
            bool(
                record
                and record.status is JobStatus.COMPLETED
                and idle
                and self.workspace
                and self._job_directory_is_valid(Path(record.job_directory))
                and self._job_has_exportable_files(Path(record.job_directory))
            )
        )
        self.review_button.setEnabled(
            bool(
                record
                and idle
                and self.workspace
                and self._job_directory_is_valid(Path(record.job_directory))
                and (
                    (
                        Path(record.job_directory)
                        / (
                            "outputs/belge-turkce.jsonl"
                            if has_document_translation
                            else "work/belge-kaynagi.txt"
                        )
                    ).is_file()
                    and not (
                        Path(record.job_directory)
                        / (
                            "outputs/belge-turkce.jsonl"
                            if has_document_translation
                            else "work/belge-kaynagi.txt"
                        )
                    ).is_symlink()
                    if record.job_kind is JobKind.DOCUMENT
                    else (Path(record.job_directory) / "outputs/turkce.srt").is_file()
                )
            )
        )
        self.summary_review_button.setEnabled(
            bool(
                record
                and idle
                and self.workspace
                and self._job_directory_is_valid(Path(record.job_directory))
                and (
                    Path(record.job_directory)
                    / (
                        "outputs/belge-ozeti.md"
                        if record.job_kind is JobKind.DOCUMENT
                        else "outputs/ozet.md"
                    )
                ).is_file()
                and not (
                    Path(record.job_directory)
                    / (
                        "outputs/belge-ozeti.md"
                        if record.job_kind is JobKind.DOCUMENT
                        else "outputs/ozet.md"
                    )
                ).is_symlink()
            )
        )
        self.open_dub_button.setEnabled(
            bool(
                record
                and idle
                and self.workspace
                and self._job_directory_is_valid(Path(record.job_directory))
                and (Path(record.job_directory) / "outputs/turkce-dublaj.mp4").is_file()
            )
        )
        self.cleanup_button.setEnabled(
            bool(
                record
                and record.status is JobStatus.COMPLETED
                and idle
                and self.workspace
                and self._job_directory_is_valid(Path(record.job_directory))
            )
        )
        if record is not None:
            job_directory = Path(record.job_directory)
            missing_result = False if record.job_kind is JobKind.DOCUMENT else (
                not (job_directory / "outputs/turkce.srt").is_file()
                or not (job_directory / "outputs/ozet.md").is_file()
                or (
                    record.source_kind != "subtitle"
                    and not (job_directory / "outputs/turkce-dublaj.mp4").is_file()
                )
            )
        else:
            missing_result = False
        self.add_outputs_button.setEnabled(
            bool(
                record
                and record.status is JobStatus.COMPLETED
                and missing_result
                and idle
                and self.workspace
                and self._job_directory_is_valid(Path(record.job_directory))
            )
        )
        review_ready = False
        can_undo = False
        if (
            record
            and record.status is JobStatus.COMPLETED
            and idle
            and self.workspace
            and self._job_directory_is_valid(Path(record.job_directory))
        ):
            outputs = Path(record.job_directory) / "outputs"
            has_translation = (
                (outputs / "turkce.srt").is_file()
                and (outputs / "turkce.kalite.json").is_file()
            )
            has_summary = all(
                (outputs / name).is_file()
                for name in ("ozet.md", "ozet.kaynaklar.json", "ozet.kalite.json")
            )
            review_ready = bool(
                self._review_source_path(Path(record.job_directory))
                and (has_translation or has_summary)
            )
            can_undo = review_can_undo(record.job_directory)
        self.create_codex_package_button.setEnabled(review_ready)
        self.import_codex_package_button.setEnabled(review_ready)
        self.undo_codex_review_button.setEnabled(can_undo)
        self.open_output_button.setEnabled(
            bool(idle and self._selected_output_directory() is not None)
        )
        self.open_final_video_button.setEnabled(
            bool(idle and self._selected_final_video() is not None)
        )
        last_export = self.preferences.last_export_directory
        self.open_last_export_button.setEnabled(
            bool(
                idle
                and last_export
                and Path(last_export).is_dir()
                and not Path(last_export).is_symlink()
            )
        )
        if record and record.job_kind in {JobKind.MEDIA, JobKind.IMAGE}:
            for button in (
                self.review_button, self.summary_review_button, self.open_dub_button,
                self.add_outputs_button, self.create_codex_package_button,
                self.import_codex_package_button, self.undo_codex_review_button,
            ):
                button.setEnabled(False)

    def _job_directory_is_valid(self, job_directory: Path) -> bool:
        if self.workspace is None:
            return False
        try:
            return job_directory.expanduser().resolve().is_relative_to(
                self.workspace.jobs.resolve()
            )
        except OSError:
            return False

    @staticmethod
    def _job_has_exportable_files(job_directory: Path) -> bool:
        return any(
            item.is_file()
            and not item.name.startswith("._")
            and not item.name.endswith((".part", ".partial"))
            for directory in (job_directory / "source", job_directory / "outputs")
            if directory.is_dir()
            for item in directory.iterdir()
        )

    @staticmethod
    def _review_source_path(job_directory: Path) -> Path | None:
        work = sorted((job_directory / "work").glob("transcript*.srt"))
        source_directory = job_directory / "source"
        if (
            source_directory.is_symlink()
            or not source_directory.resolve().is_relative_to(job_directory)
        ):
            raise RuntimeError("Belge kaynak klasörü güvenli iş alanının dışında.")
        ready = (
            sorted(source_directory.glob("source.*-orig.srt"))
            + sorted(source_directory.glob("source.*.srt"))
            if source_directory.is_dir()
            else []
        )
        return next(
            (
                item
                for item in (*work, *ready)
                if item.is_file() and not item.name.startswith("._")
            ),
            None,
        )

    @staticmethod
    def _summary_source_title(job_directory: Path, record: JobRecord) -> str:
        manifest_path = job_directory / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            preflight = manifest.get("preflight") if isinstance(manifest, dict) else None
            title = preflight.get("title") if isinstance(preflight, dict) else None
            if title:
                return str(title)[:300]
        except (OSError, json.JSONDecodeError):
            pass
        if record.source_kind in {"file", "subtitle"}:
            return Path(record.source_reference).name
        return record.source_reference[:300]

    def _codex_review_paths(self, record: JobRecord) -> dict[str, object]:
        job_directory = Path(record.job_directory)
        source = self._review_source_path(job_directory)
        if source is None:
            raise ValueError("Bu işin kaynak altyazısı bulunamadı.")
        outputs = job_directory / "outputs"
        translated = outputs / "turkce.srt"
        summary = outputs / "ozet.md"
        summary_trace = outputs / "ozet.kaynaklar.json"
        has_summary = (
            summary.is_file()
            and summary_trace.is_file()
            and (outputs / "ozet.kalite.json").is_file()
        )
        return {
            "expected_job_id": record.id,
            "source_srt": source,
            "translated_srt": translated if translated.is_file() else None,
            "glossary_path": _bundled_glossary_path(),
            "summary_path": summary if has_summary else None,
            "summary_trace_path": summary_trace if has_summary else None,
        }

    def _create_codex_package(self) -> None:
        record = self._selected_record()
        if record is None or self._process_is_running():
            return
        job_directory = Path(record.job_directory)
        try:
            paths = self._codex_review_paths(record)
            package = create_review_package(
                job_id=record.id,
                source_language=record.source_language,
                source_srt=paths["source_srt"],
                translated_srt=paths["translated_srt"],
                glossary_path=paths["glossary_path"],
                destination_parent=job_directory / "review/packages",
                summary_path=paths["summary_path"],
                summary_trace_path=paths["summary_trace_path"],
                tool_manifest_path=_bundled_tool_manifest_path(),
            )
            desktop_copy = copy_review_package(
                package,
                desktop=Path.home() / "Desktop",
                folder_name=f"KSI Local Studio İnceleme {record.id[:8]}",
            )
        except (OSError, RuntimeError, ValueError) as error:
            safe_message = self._actionable_message(
                redact_sensitive_text(str(error)),
                self._t("review_package.create_action"),
            )
            self.status.setText(safe_message)
            QMessageBox.critical(self, self._t("review_package.create_failed"), safe_message)
            return
        message = self._t("review_package.ready")
        self.status.setText(message)
        self.store.append_log(record.id, f"Harici inceleme paketi oluşturuldu: {package.name}")
        QMessageBox.information(
            self,
            self._t("review_package.ready_title"),
            message + "\n\n" + self._t("review_package.folder", path=desktop_copy),
        )

    def _review_preview_text(self, preview: ReviewPreview) -> str:
        changes = [*preview.segment_changes, *preview.summary_changes]
        lines = [
            self._t("review_package.subtitle_changes", count=len(preview.segment_changes)),
            self._t("review_package.summary_changes", count=len(preview.summary_changes)),
            self._t("review_package.quality_warnings", count=preview.subtitle_warning_count),
        ]
        if changes:
            lines.extend(("", self._t("review_package.preview")))
        for change in changes[:8]:
            old = change.old_text.replace("\n", " ")[:120]
            new = change.new_text.replace("\n", " ")[:120]
            lines.append(f"• {change.item_id}: {old} → {new}")
        if len(changes) > 8:
            lines.append(self._t("review_package.more", count=len(changes) - 8))
        return "\n".join(lines)

    def _import_codex_package(self) -> None:
        record = self._selected_record()
        if record is None or self._process_is_running():
            return
        selected = QFileDialog.getExistingDirectory(
            self,
            self._t("review_package.choose"),
            str(Path.home() / "Desktop"),
        )
        if not selected:
            return
        job_directory = Path(record.job_directory)
        outputs = job_directory / "outputs"
        try:
            paths = self._codex_review_paths(record)
            preview = preview_review_package(package_directory=selected, **paths)
        except (OSError, RuntimeError, ValueError) as error:
            safe_message = self._actionable_message(
                redact_sensitive_text(str(error)),
                self._t("review_package.invalid_action"),
            )
            QMessageBox.critical(self, self._t("review_package.invalid_title"), safe_message)
            return
        if preview.changed_count == 0:
            QMessageBox.information(
                self,
                self._t("review_package.no_changes_title"),
                self._t("review_package.no_changes"),
            )
            return
        answer = QMessageBox.question(
            self,
            self._t("review_package.apply_title"),
            self._review_preview_text(preview)
            + "\n\n"
            + self._t("review_package.apply_confirm"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            result = apply_review_package(
                package_directory=selected,
                job_directory=job_directory,
                quality_path=outputs / "turkce.kalite.json",
                summary_quality_path=outputs / "ozet.kalite.json",
                expected_digest=preview.package_digest,
                **paths,
            )
        except (OSError, RuntimeError, ValueError) as error:
            safe_message = self._actionable_message(
                redact_sensitive_text(str(error)),
                self._t("review_package.apply_action"),
            )
            self.status.setText(safe_message)
            QMessageBox.critical(self, self._t("review_package.apply_failed"), safe_message)
            return
        self.store.append_log(
            record.id,
            f"Harici düzeltme uygulandı: {result.revision_id} "
            f"({len(result.segment_ids)} altyazı, {len(result.summary_statement_ids)} özet)",
        )
        if result.segment_ids and record.want_dub:
            try:
                updated = self.store.reopen_after_review(record.id, result.dependent_stages)
                self._start(existing_job=updated)
            except (OSError, RuntimeError, ValueError) as error:
                safe_message = self._actionable_message(
                    redact_sensitive_text(str(error)),
                    self._t("review_package.dub_action"),
                )
                self.status.setText(self._t("review_package.dub_failed_status", message=safe_message))
                self._refresh_history()
                QMessageBox.warning(
                    self,
                    self._t("review_package.dub_failed_title"),
                    self._t("review_package.dub_failed_body", message=safe_message),
                )
            return
        self.status.setText(self._t("review_package.applied_status"))
        self._refresh_history()
        QMessageBox.information(
            self,
            self._t("review_package.applied_title"),
            self._t("review_package.applied_body"),
        )

    def _undo_codex_review(self) -> None:
        record = self._selected_record()
        if record is None or self._process_is_running():
            return
        answer = QMessageBox.question(
            self,
            self._t("review_package.undo_title"),
            self._t("review_package.undo_confirm"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            result = undo_last_review(
                job_directory=record.job_directory,
                outputs_directory=Path(record.job_directory) / "outputs",
            )
        except (OSError, RuntimeError, ValueError) as error:
            safe_message = self._actionable_message(
                redact_sensitive_text(str(error)),
                self._t("review_package.undo_action"),
            )
            QMessageBox.critical(self, self._t("review_package.undo_failed"), safe_message)
            return
        self.store.append_log(record.id, f"Harici düzeltme geri alındı: {result.revision_id}")
        if result.segment_ids and record.want_dub:
            try:
                updated = self.store.reopen_after_review(record.id, result.dependent_stages)
                self._start(existing_job=updated)
            except (OSError, RuntimeError, ValueError) as error:
                safe_message = self._actionable_message(
                    redact_sensitive_text(str(error)),
                    self._t("review_package.dub_action"),
                )
                self.status.setText(self._t("review_package.undo_dub_status", message=safe_message))
                self._refresh_history()
                QMessageBox.warning(self, self._t("review_package.dub_failed_title"), safe_message)
            return
        self.status.setText(self._t("review_package.undo_status"))
        self._refresh_history()

    def _review_subtitle(self) -> None:
        record = self._selected_record()
        job_directory = Path(record.job_directory) if record else self.current_job
        if job_directory is None or not self._job_directory_is_valid(job_directory):
            return
        if record is not None and record.job_kind is JobKind.DOCUMENT:
            translated = job_directory / "outputs/belge-turkce.jsonl"
            translation_quality = job_directory / "outputs/belge-turkce.kalite.json"
            if (
                translated.is_file()
                and not translated.is_symlink()
                and translation_quality.is_file()
                and not translation_quality.is_symlink()
            ):
                try:
                    dialog = DocumentTranslationReviewDialog(
                        translated,
                        quality_path=translation_quality,
                        language=self.preferences.ui_language,
                        parent=self,
                    )
                except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
                    QMessageBox.critical(
                        self,
                        self._t("media.document_translation_open_failed"),
                        self._actionable_message(
                            str(error),
                            self._t("media.document_translation_action"),
                        ),
                    )
                    return
                dialog.exec()
                return
            extracted = job_directory / "work/belge-kaynagi.txt"
            if (
                not extracted.is_file()
                or extracted.is_symlink()
                or not extracted.resolve().is_relative_to(job_directory.resolve())
            ):
                QMessageBox.information(
                    self,
                    self._t("media.document_text_missing_title"),
                    self._t("media.document_text_missing"),
                )
                return
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(extracted))):
                QMessageBox.critical(
                    self,
                    self._t("media.document_text_open_failed"),
                    self._actionable_message(
                        self._t("media.viewer_failed"),
                        self._t("media.document_text_action"),
                    ),
                )
            return
        translated = job_directory / "outputs/turkce.srt"
        if not translated.is_file():
            QMessageBox.information(
                self,
                self._t("media.subtitle_missing_title"),
                self._t("media.subtitle_missing"),
            )
            return
        try:
            dialog = SubtitleReviewDialog(
                translated,
                source_path=self._review_source_path(job_directory),
                quality_path=job_directory / "outputs/turkce.kalite.json",
                language=self.preferences.ui_language,
                parent=self,
            )
        except (OSError, ValueError) as error:
            QMessageBox.critical(
                self,
                self._t("media.subtitle_open_failed"),
                self._actionable_message(
                    str(error), self._t("media.subtitle_action")
                ),
            )
            return
        dialog.exec()

    def _review_summary(self) -> None:
        record = self._selected_record()
        job_directory = Path(record.job_directory) if record else self.current_job
        if job_directory is None or not self._job_directory_is_valid(job_directory):
            return
        is_document = bool(record and record.job_kind is JobKind.DOCUMENT)
        summary = job_directory / (
            "outputs/belge-ozeti.md" if is_document else "outputs/ozet.md"
        )
        if (
            not summary.is_file()
            or summary.is_symlink()
            or not summary.resolve().is_relative_to(job_directory.resolve())
        ):
            QMessageBox.information(
                self,
                self._t("media.summary_missing_title"),
                self._t("media.summary_missing"),
            )
            return
        try:
            dialog = SummaryReviewDialog(
                summary,
                quality_path=job_directory
                / (
                    "outputs/belge-ozeti.kalite.json"
                    if is_document
                    else "outputs/ozet.kalite.json"
                ),
                language=self.preferences.ui_language,
                parent=self,
            )
        except (OSError, ValueError) as error:
            QMessageBox.critical(
                self,
                self._t("media.summary_open_failed"),
                self._actionable_message(
                    str(error), self._t("media.summary_action")
                ),
            )
            return
        dialog.exec()

    def _open_dub(self) -> None:
        record = self._selected_record()
        job_directory = Path(record.job_directory) if record else self.current_job
        if job_directory is None or not self._job_directory_is_valid(job_directory):
            return
        dubbed = job_directory / "outputs/turkce-dublaj.mp4"
        if not dubbed.is_file():
            QMessageBox.information(
                self,
                self._t("media.dub_missing_title"),
                self._t("media.dub_missing"),
            )
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(dubbed))):
            QMessageBox.critical(
                self,
                self._t("media.dub_open_failed"),
                self._actionable_message(
                    self._t("media.player_failed"),
                    self._t("media.dub_action"),
                ),
            )

    def _retry_selected(self) -> None:
        record = self._selected_record()
        if record is None or self.workspace is None or self._process_is_running():
            return
        try:
            if record.job_kind in {JobKind.MEDIA, JobKind.IMAGE}:
                self.tool_controller.resume(record.id)
                return
            self._start(existing_job=record)
        except (OSError, RuntimeError, ValueError) as error:
            safe_message = self._actionable_message(
                redact_sensitive_text(str(error)),
                self._t("job.retry_action"),
            )
            self.status.setText(safe_message)
            self._refresh_history()
            QMessageBox.critical(self, self._t("job.retry_failed"), safe_message)

    def _add_outputs_selected(self) -> None:
        record = self._selected_record()
        if record is None or self.workspace is None or self._process_is_running():
            return
        job_directory = Path(record.job_directory)
        add_subtitle = not (job_directory / "outputs/turkce.srt").is_file()
        add_summary = not (job_directory / "outputs/ozet.md").is_file()
        add_dub = (
            Path(record.source_reference).suffix.casefold() not in TEXT_SOURCE_SUFFIXES
            and not (job_directory / "outputs/turkce-dublaj.mp4").is_file()
        )
        labels = []
        if add_subtitle:
            labels.append(self._t("job.add_subtitle"))
        if add_summary:
            labels.append(self._t("job.add_summary"))
        if add_dub:
            labels.append(self._t("job.add_dub"))
        if not labels:
            return
        answer = QMessageBox.question(
            self,
            self._t("job.add_outputs_title"),
            self._t("job.add_outputs_body", outputs=", ".join(labels)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            updated = self.store.extend_job_outputs(
                record.id,
                want_subtitle=add_subtitle,
                want_summary=add_summary,
                want_dub=add_dub,
            )
            self._start(existing_job=updated)
        except (OSError, RuntimeError, ValueError) as error:
            safe_message = self._actionable_message(
                redact_sensitive_text(str(error)),
                self._t("job.add_outputs_action"),
            )
            self.status.setText(safe_message)
            self._refresh_history()
            QMessageBox.critical(self, self._t("job.add_outputs_failed"), safe_message)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            self._t("file.choose_source"),
            str(Path.home()),
            self._t("file.supported")
            + ";;Image (*.jpg *.jpeg *.png *.webp *.tif *.tiff)",
        )
        if path:
            self.source.setText(path)

    def _source_is_document(self, value: str | None = None) -> bool:
        selected = str(self.job_kind.currentData() or "auto")
        if selected == "document":
            return True
        if selected == "video":
            return False
        source = (self.source.text() if value is None else value).strip()
        return bool(
            source
            and not source.casefold().startswith("https://")
            and Path(source).suffix.casefold() in DOCUMENT_SUFFIXES
        )

    def _source_is_image(self, value: str | None = None) -> bool:
        source = (self.source.text() if value is None else value).strip()
        return bool(
            source
            and not source.casefold().startswith("https://")
            and Path(source).suffix.casefold() in SUPPORTED_IMAGE_SUFFIXES
        )

    def _job_kind_changed(self) -> None:
        self._sync_kind_cards()
        self._source_changed(self.source.text())

    def _sync_kind_cards(self) -> None:
        if not hasattr(self, "video_card"):
            return
        selected = str(self.job_kind.currentData() or "auto")
        self.video_card.setChecked(selected == "video")
        self.document_card.setChecked(selected == "document")
        self.video_card.setText(
            ("✓  " if selected == "video" else "") + self._t("card.video")
        )
        self.document_card.setText(
            ("✓  " if selected == "document" else "") + self._t("card.document")
        )
        if selected == "video":
            self.kind_hint.setText(self._t("hint.kind_video"))
        elif selected == "document":
            self.kind_hint.setText(self._t("hint.kind_document"))
        else:
            self.kind_hint.setText(self._t("hint.kind_auto"))

    def _select_kind_card(self, kind: str, checked: bool) -> None:
        selected = kind if checked else str(self.job_kind.currentData() or "auto")
        index = self.job_kind.findData(selected)
        if index >= 0:
            self.job_kind.setCurrentIndex(index)
        self._sync_kind_cards()

    def _select_document_glossary(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            self._t("glossary.choose"),
            str(Path.home()),
            self._t("glossary.filter"),
        )
        if not path:
            return
        try:
            details = validate_document_glossary(path)
        except (OSError, RuntimeError, ValueError) as error:
            QMessageBox.critical(
                self,
                self._t("glossary.invalid_title"),
                self._actionable_message(
                    str(error), self._t("glossary.invalid_action")
                ),
            )
            return
        self.document_glossary_path = Path(path).expanduser().resolve()
        self.document_glossary_label.setText(self._t(
            "glossary.selected",
            name=self.document_glossary_path.name,
            languages=int(details["language_count"]),
            preserved=int(details["preserve_count"]),
        ))
        self.document_glossary_clear.setEnabled(True)

    def _clear_document_glossary(self) -> None:
        self.document_glossary_path = None
        self.document_glossary_label.setText(self._t("optional"))
        self.document_glossary_clear.setEnabled(False)

    def _document_summary_source_changed(self) -> None:
        if (
            self._source_is_document()
            and self.document_summary_source.currentData() == "translation"
            and self.want_summary.isChecked()
        ):
            self.want_subtitle.setChecked(True)

    def _document_translation_option_changed(self, checked: bool) -> None:
        if (
            not checked
            and self._source_is_document()
            and self.document_summary_source.currentData() == "translation"
        ):
            self.document_summary_source.setCurrentIndex(0)

    def _document_summary_options_changed(self) -> None:
        enabled = self._source_is_document() and self.want_summary.isChecked()
        self.document_summary_profile.setEnabled(enabled)
        self.document_summary_source.setEnabled(enabled)
        self.document_summary_pdf.setEnabled(enabled)
        if enabled:
            self._document_summary_source_changed()

    def _source_changed(self, value: str) -> None:
        is_document = self._source_is_document(value)
        self.document_glossary_title.setVisible(is_document)
        self.document_glossary_widget.setVisible(is_document)
        for widget in (
            self.document_summary_profile_title,
            self.document_summary_profile,
            self.document_summary_source_title,
            self.document_summary_source,
            self.document_summary_pdf_title,
            self.document_summary_pdf,
        ):
            widget.setVisible(is_document)
        self.document_glossary_clear.setEnabled(
            is_document and self.document_glossary_path is not None
        )
        self.want_subtitle.setText(
            self._t("output.document_translation")
            if is_document
            else self._t("output.subtitle")
        )
        if is_document:
            self.download_only.setChecked(False)
            self.download_only.setEnabled(False)
            self.want_dub.setChecked(False)
            self.want_dub.setEnabled(False)
        else:
            self.download_only.setEnabled(True)
            self.want_dub.setEnabled(not self.download_only.isChecked())
        self._document_summary_options_changed()
        is_x = False
        is_udemy = False
        if value.strip().casefold().startswith("https://"):
            try:
                platform = validate_source_url(value).platform
                is_x = platform is Platform.X
                is_udemy = platform is Platform.UDEMY
            except SourceURLValidationError:
                pass
        uses_session = is_x or is_udemy
        selected_platform = Platform.UDEMY if is_udemy else Platform.X if is_x else None
        if (
            self.browser_profile is not None
            and self.browser_profile_platform is not None
            and self.browser_profile_platform is not selected_platform
        ):
            self.browser_profile = None
            self.browser_profile_platform = None
            self.use_browser_session.setChecked(False)
        self.use_browser_session.setEnabled(uses_session)
        self.browser_kind.setEnabled(uses_session)
        self.select_profile_button.setEnabled(uses_session)
        self.udemy_access_confirmed.setVisible(is_udemy)
        self.udemy_access_confirmed.setEnabled(is_udemy)
        self.use_browser_session.setText(
            self._t("session.use_udemy")
            if is_udemy
            else self._t("session.use_x")
        )
        if not uses_session:
            self.use_browser_session.setChecked(False)
        if not is_udemy:
            self.udemy_access_confirmed.setChecked(False)
        if is_udemy and self.browser_profile is None:
            self.browser_profile_label.setText(self._t("session.udemy_required_hint"))
        if hasattr(self, "start_button") and not self._process_is_running():
            self.start_button.setEnabled(
                self.workspace is not None or self._source_is_image(value)
            )

    def _select_browser_profile(self) -> None:
        is_udemy = False
        try:
            is_udemy = validate_source_url(self.source.text()).platform is Platform.UDEMY
        except SourceURLValidationError:
            pass
        service = "Udemy" if is_udemy else "X"
        selected = QFileDialog.getExistingDirectory(
            self,
            self._t("session.choose_profile", service=service),
            str(Path.home()),
        )
        if not selected:
            return
        try:
            session = validate_browser_session(
                str(self.browser_kind.currentData()), selected
            )
        except ValueError as error:
            QMessageBox.critical(
                self,
                self._t("session.invalid_title"),
                self._actionable_message(
                    str(error), self._t("session.invalid_action")
                ),
            )
            return
        answer = QMessageBox.question(
            self,
            self._t("session.confirm_title", service=service),
            (
                self._t("session.confirm_udemy")
                if is_udemy
                else self._t("session.confirm_x")
            )
            + "\n\n"
            + self._t("session.privacy"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.browser_profile = session.profile_directory
        self.browser_profile_platform = Platform.UDEMY if is_udemy else Platform.X
        self.use_browser_session.setChecked(True)
        self.browser_profile_label.setText(self._t("session.selected", name=session.profile_directory.name))

    def _browser_session_from_snapshot(
        self, snapshot: PreflightSnapshot
    ) -> BrowserSession | None:
        if snapshot.browser is None or snapshot.browser_profile is None:
            return None
        return validate_browser_session(snapshot.browser, snapshot.browser_profile)

    def _download_only_toggled(self, checked: bool) -> None:
        if checked:
            self.want_subtitle.setChecked(False)
            self.want_summary.setChecked(False)
            self.want_dub.setChecked(False)
        self.want_subtitle.setEnabled(not checked)
        self.want_summary.setEnabled(not checked)
        self.want_dub.setEnabled(not checked and not self._source_is_document())

    def _snapshot(self) -> PreflightSnapshot:
        profile = (
            str(self.browser_profile)
            if self.use_browser_session.isChecked() and self.browser_profile is not None
            else None
        )
        return PreflightSnapshot(
            source=self.source.text().strip(),
            job_kind=str(self.job_kind.currentData() or "auto"),
            source_language=str(self.language.currentData()),
            download_only=self.download_only.isChecked(),
            want_subtitle=self.want_subtitle.isChecked(),
            want_summary=self.want_summary.isChecked(),
            want_dub=self.want_dub.isChecked(),
            browser=(str(self.browser_kind.currentData()) if profile else None),
            browser_profile=profile,
            udemy_access_confirmed=self.udemy_access_confirmed.isChecked(),
            document_glossary=(
                str(self.document_glossary_path)
                if self._source_is_document() and self.document_glossary_path is not None
                else None
            ),
            document_summary_profile=str(self.document_summary_profile.currentData()),
            document_summary_source=str(self.document_summary_source.currentData()),
            document_summary_pdf=self.document_summary_pdf.isChecked(),
        )

    def _find_sidecar(self, video: Path, language: str) -> Path | None:
        selection = discover_best_subtitle(
            video.parent,
            requested_language=language,
            stem_prefix=video.stem,
        )
        return selection.path if selection is not None else None

    def _start(self, _checked: bool = False, *, existing_job: JobRecord | None = None) -> None:
        if self.maintenance_pending:
            return
        raw = existing_job.source_reference if existing_job else self.source.text().strip()
        if raw and existing_job is None and self._source_is_image(raw):
            try:
                ImageToolsDialog(raw, self.preferences.ui_language, self).exec()
            except (OSError, RuntimeError, ValueError) as error:
                QMessageBox.critical(
                    self,
                    "KSI Local Studio",
                    self._actionable_message(
                        redact_sensitive_text(str(error)),
                        "Başka bir görsel seçin veya hedef ölçüyü küçültün.",
                    ),
                )
            return
        if self.workspace is None:
            self._load_workspace()
            if self.workspace is None:
                return
        if not raw:
            QMessageBox.warning(
                self,
                self._t("start.source_missing_title"),
                self._actionable_message(
                    self._t("start.source_missing"),
                    self._t("start.source_missing_action"),
                ),
            )
            return
        if existing_job is not None:
            kind_index = self.job_kind.findData(existing_job.job_kind.value)
            if kind_index >= 0:
                self.job_kind.setCurrentIndex(kind_index)
            language_index = self.language.findData(existing_job.source_language)
            if language_index >= 0:
                self.language.setCurrentIndex(language_index)
            self.want_subtitle.setChecked(existing_job.want_subtitle)
            self.want_summary.setChecked(existing_job.want_summary)
            self.want_dub.setChecked(existing_job.want_dub)
            self.download_only.setChecked(existing_job.download_only)
            profile_index = self.document_summary_profile.findData(
                existing_job.summary_profile
            )
            self.document_summary_profile.setCurrentIndex(max(profile_index, 0))
            summary_source_index = self.document_summary_source.findData(
                existing_job.document_summary_source
            )
            self.document_summary_source.setCurrentIndex(max(summary_source_index, 0))
            self.document_summary_pdf.setChecked(existing_job.document_summary_pdf)
            self.source.setText(raw)
            if existing_job.job_kind is JobKind.DOCUMENT:
                translation_outputs = (
                    Path(existing_job.job_directory) / "outputs/belge-turkce.jsonl",
                    Path(existing_job.job_directory) / "outputs/belge-turkce.txt",
                    Path(existing_job.job_directory) / "outputs/belge-turkce.md",
                    Path(existing_job.job_directory) / "outputs/belge-turkce.docx",
                    Path(existing_job.job_directory) / "outputs/belge-turkce.pdf",
                    Path(existing_job.job_directory) / "outputs/belge-turkce.kalite.json",
                )
                summary_outputs = [
                    Path(existing_job.job_directory) / "outputs/belge-ozeti.md",
                    Path(existing_job.job_directory) / "outputs/belge-ozeti.docx",
                    Path(existing_job.job_directory) / "outputs/belge-ozeti.kaynaklar.json",
                    Path(existing_job.job_directory) / "outputs/belge-ozeti.kalite.json",
                ]
                if existing_job.document_summary_pdf:
                    summary_outputs.append(
                        Path(existing_job.job_directory) / "outputs/belge-ozeti.pdf"
                    )
                extraction_outputs = (
                    Path(existing_job.job_directory) / "work/belge-kaynagi.jsonl",
                    Path(existing_job.job_directory) / "work/belge-kaynagi.txt",
                    Path(existing_job.job_directory) / "work/belge-kaynagi.kalite.json",
                )
                translation_ready = all(
                    item.is_file() and not item.is_symlink()
                    for item in translation_outputs
                )
                extraction_ready = all(
                    item.is_file()
                    and not item.is_symlink()
                    and item.resolve().is_relative_to(
                        Path(existing_job.job_directory).resolve()
                    )
                    for item in extraction_outputs
                )
                summary_ready = all(
                    item.is_file() and not item.is_symlink() for item in summary_outputs
                )
                requested_ready = (
                    (not existing_job.want_subtitle or translation_ready)
                    and (not existing_job.want_summary or summary_ready)
                )
                if requested_ready:
                    QMessageBox.information(
                        self,
                        self._t("start.document_ready_title"),
                        self._t("start.document_ready"),
                    )
                    return
                if extraction_ready:
                    self._launch_document_remaining(existing_job)
                    return
                self._launch_document_extraction(existing_job)
                return
            try:
                existing_platform = (
                    validate_source_url(raw).platform
                    if raw.casefold().startswith("https://")
                    else None
                )
            except SourceURLValidationError:
                existing_platform = None
            if existing_platform is not Platform.UDEMY:
                if existing_job.status is not JobStatus.QUEUED:
                    existing_job = self.store.retry_job(existing_job.id)
                self._launch_job(
                    raw,
                    existing_job,
                    existing_job.max_height,
                    media_index=existing_job.media_index,
                )
                return
        if not (
            self.download_only.isChecked()
            or self.want_subtitle.isChecked()
            or self.want_summary.isChecked()
            or self.want_dub.isChecked()
        ):
            QMessageBox.warning(
                self,
                self._t("start.output_missing_title"),
                self._actionable_message(
                    self._t("start.output_missing"),
                    self._t("start.output_missing_action"),
                ),
            )
            return

        if raw.lower().startswith("https://"):
            try:
                platform = validate_source_url(raw).platform
            except SourceURLValidationError as error:
                QMessageBox.critical(
                    self,
                    self._t("start.link_unsupported_title"),
                    self._actionable_message(
                        str(error),
                        self._t("start.link_action"),
                    ),
                )
                return
        elif not Path(raw).expanduser().resolve().is_file():
            QMessageBox.critical(
                self,
                self._t("start.file_missing_title"),
                self._actionable_message(
                    self._t("start.file_missing"),
                    self._t("start.file_missing_action"),
                ),
            )
            return

        if raw.lower().startswith("https://") and platform is Platform.UDEMY:
            if not self.udemy_access_confirmed.isChecked():
                QMessageBox.warning(
                    self,
                    self._t("start.udemy_access_title"),
                    self._actionable_message(
                        self._t("start.udemy_access"),
                        self._t("start.udemy_access_action"),
                    ),
                )
                return
            if not self.use_browser_session.isChecked() or self.browser_profile is None:
                QMessageBox.warning(
                    self,
                    self._t("start.udemy_profile_title"),
                    self._actionable_message(
                        self._t("start.udemy_profile"),
                        self._t("start.udemy_profile_action"),
                    ),
                )
                return

        if self.use_browser_session.isChecked() and self.browser_profile is None:
            QMessageBox.warning(
                self,
                self._t("start.profile_missing_title"),
                self._actionable_message(
                    self._t("start.profile_missing"),
                    self._t("start.profile_missing_action"),
                ),
            )
            return

        if self.preflight_pending:
            return
        snapshot = self._snapshot()
        self.preflight_existing_job = existing_job
        self.preflight_pending = True
        self.start_button.setEnabled(False)
        self.retry_button.setEnabled(False)
        self.progress.setRange(0, 0)
        self.status.setText(self._t("preflight.running"))
        self.preflight_thread = threading.Thread(
            target=self._inspect_in_background,
            args=(snapshot,),
            name="KSI Local Studio-On-Inceleme",
            daemon=True,
        )
        self.preflight_thread.start()

    def _inspect_in_background(self, snapshot: PreflightSnapshot) -> None:
        try:
            if self.workspace is None:
                raise RuntimeError("Harici SSD kullanılamıyor.")
            result = inspect_source(
                snapshot.source,
                workspace=self.workspace,
                download_only=snapshot.download_only,
                want_subtitle=snapshot.want_subtitle,
                want_summary=snapshot.want_summary,
                want_dub=snapshot.want_dub,
                ffmpeg_path=str(tool_path("ffmpeg")),
                ffprobe_path=str(tool_path("ffprobe")),
                browser_session=self._browser_session_from_snapshot(snapshot),
                udemy_access_confirmed=snapshot.udemy_access_confirmed,
                job_kind=snapshot.job_kind,
            )
        except (OSError, RuntimeError, ValueError) as error:
            result = None
            inspection_error: RuntimeError | None = RuntimeError(
                redact_sensitive_text(str(error))
            )
        else:
            inspection_error = None
        try:
            self.preflight_bridge.finished.emit(result, inspection_error, snapshot)
        except RuntimeError:
            return

    def _preflight_finished(
        self,
        result: PreflightResult | None,
        error: RuntimeError | None,
        snapshot: PreflightSnapshot,
    ) -> None:
        existing_job = self.preflight_existing_job
        self.preflight_existing_job = None
        self.preflight_pending = False
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.start_button.setEnabled(self.workspace is not None and not self._process_is_running())
        if snapshot != self._snapshot():
            self.status.setText(self._t("preflight.changed"))
            return
        if result is None:
            safe_message = self._actionable_message(
                redact_sensitive_text(str(error or self._t("preflight.incomplete"))),
                self._t("preflight.retry_action"),
            )
            self.status.setText(safe_message)
            QMessageBox.critical(self, self._t("preflight.failed_title"), safe_message)
            return
        dialog = PreflightDialog(
            result, self, language=self.preferences.ui_language
        )
        if existing_job is not None and dialog.height_combo.count():
            saved_height = dialog.height_combo.findData(existing_job.max_height)
            if saved_height >= 0:
                dialog.height_combo.setCurrentIndex(saved_height)
                dialog.height_combo.setEnabled(False)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self.status.setText(
                self._t("preflight.document_cancelled")
                if result.document is not None
                else self._t("preflight.download_cancelled")
            )
            return
        if self.workspace is None:
            QMessageBox.critical(
                self,
                self._t("preflight.storage_unavailable_title"),
                self._actionable_message(
                    self._t("preflight.storage_removed"),
                    self._t("preflight.storage_removed_action"),
                ),
            )
            return
        selected_choice = (
            result.choice(
                dialog.selected_height,
                media_index=max(dialog.selected_media_index, 1),
            )
            if result.format_choices
            else result.fixed_budget
        )
        try:
            current_free = shutil.disk_usage(self.workspace.root).free
        except OSError:
            current_free = 0
        if selected_choice is not None and current_free < selected_choice.required_bytes:
            self.status.setText(self._t("preflight.space_changed"))
            QMessageBox.critical(
                self,
                self._t("preflight.space_title"),
                self._actionable_message(
                    self._t("preflight.space_reduced"),
                    self._t("preflight.space_action"),
                ),
            )
            return
        if result.document is not None:
            self._begin_document_import(snapshot, result)
            return
        if existing_job is not None and existing_job.status is not JobStatus.QUEUED:
            try:
                existing_job = self.store.retry_job(existing_job.id)
            except (OSError, RuntimeError, ValueError) as retry_error:
                safe_message = self._actionable_message(
                    redact_sensitive_text(str(retry_error)),
                    self._t("job.retry_action"),
                )
                self.status.setText(safe_message)
                QMessageBox.critical(self, self._t("job.retry_failed"), safe_message)
                return
        self._launch_job(
            snapshot.source,
            existing_job,
            existing_job.max_height if existing_job is not None else dialog.selected_height,
            media_index=(
                existing_job.media_index
                if existing_job is not None
                else max(dialog.selected_media_index, 1)
            ),
            preflight=result,
            browser_session=self._browser_session_from_snapshot(snapshot),
        )

    def _begin_document_import(
        self, snapshot: PreflightSnapshot, result: PreflightResult
    ) -> None:
        if self.workspace is None or result.document is None:
            return
        job_id = str(uuid.uuid4()).upper()
        job_directory = self.workspace.jobs / job_id
        self.current_job_id = job_id
        self.current_job = job_directory
        self.maintenance_pending = True
        self.document_import_cancel.clear()
        self.start_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress.setRange(0, 0)
        self.status.setText(self._t("document.importing"))
        self.document_import_thread = threading.Thread(
            target=self._import_document_in_background,
            args=(job_id, job_directory, snapshot, result),
            name="KSI Local Studio-Belge-Ice-Aktarma",
            daemon=True,
        )
        self.document_import_thread.start()

    def _import_document_in_background(
        self,
        job_id: str,
        job_directory: Path,
        snapshot: PreflightSnapshot,
        result: PreflightResult,
    ) -> None:
        try:
            if result.document is None:
                raise RuntimeError("Belge ön inceleme bilgisi bulunamadı.")
            imported = import_document_source(
                snapshot.source,
                job_directory / "source",
                expected_size=result.document.size_bytes,
                expected_sha256=result.document.sha256,
                cancel_check=self.document_import_cancel.is_set,
            )
            if snapshot.document_glossary:
                glossary_source = Path(snapshot.document_glossary).expanduser()
                validate_document_glossary(glossary_source)
                if self.document_import_cancel.is_set():
                    raise DocumentImportCancelled("Belge içe aktarma iptal edildi.")
                glossary_bytes = glossary_source.read_bytes()
                custom_target = job_directory / "source/belge-sozlugu.json"
                atomic_write_bytes(custom_target, glossary_bytes)
                validate_document_glossary(custom_target)
        except (DocumentImportCancelled, OSError, RuntimeError, ValueError) as error:
            imported = None
            import_error: RuntimeError | None = RuntimeError(
                redact_sensitive_text(str(error))
            )
            try:
                uuid.UUID(job_id)
                if job_directory.is_dir() and job_directory.name == job_id:
                    shutil.rmtree(job_directory)
            except (OSError, ValueError):
                pass
        else:
            import_error = None
        try:
            self.document_import_bridge.finished.emit(
                imported, import_error, job_id, snapshot, result
            )
        except RuntimeError:
            return

    def _document_import_finished(
        self,
        imported: ImportedDocument | None,
        error: RuntimeError | None,
        job_id: str,
        snapshot: PreflightSnapshot,
        result: PreflightResult,
    ) -> None:
        self.maintenance_pending = False
        was_cancelled = self.document_import_cancel.is_set()
        self.document_import_cancel.clear()
        self.cancel_button.setEnabled(False)
        self.progress.setRange(0, 1)
        if imported is None:
            self.progress.setValue(0)
            message = self._actionable_message(
                str(error or self._t("document.import_incomplete")),
                self._t("document.import_action"),
            )
            self.status.setText(message)
            self.start_button.setEnabled(self.workspace is not None)
            if was_cancelled:
                QMessageBox.information(
                    self,
                    self._t("document.import_cancelled_title"),
                    self._t("document.import_cancelled"),
                )
            else:
                QMessageBox.critical(
                    self,
                    self._t("document.import_failed_title"),
                    message + "\n\n" + self._t("document.no_partial"),
                )
            return
        job_directory = imported.path.parent.parent
        try:
            self.store.create_job(
                job_id=job_id,
                job_kind=JobKind.DOCUMENT,
                source=str(imported.path),
                source_language=snapshot.source_language,
                want_subtitle=snapshot.want_subtitle,
                want_summary=snapshot.want_summary,
                want_dub=False,
                job_directory=job_directory,
                summary_profile=snapshot.document_summary_profile,
                document_summary_source=snapshot.document_summary_source,
                document_summary_pdf=snapshot.document_summary_pdf,
            )
            self.store.ensure_stages(job_id, ["document_import"])
            self.store.transition_job(job_id, JobStatus.RUNNING)
            self.store.set_stage(job_id, "document_import", StageStatus.RUNNING)
            atomic_write_json(
                job_directory / "manifest.json",
                {
                    "schema_version": 2,
                    "job_kind": JobKind.DOCUMENT,
                    "preflight": result.to_dict(),
                    "import": imported.to_dict(),
                    "document_glossary": (
                        validate_document_glossary(
                            job_directory / "source/belge-sozlugu.json"
                        )
                        if (job_directory / "source/belge-sozlugu.json").is_file()
                        else None
                    ),
                    "summary_options": {
                        "profile": snapshot.document_summary_profile,
                        "source": snapshot.document_summary_source,
                        "pdf": snapshot.document_summary_pdf,
                    },
                },
            )
            self.store.set_stage(job_id, "document_import", StageStatus.COMPLETED)
            self.store.transition_job(job_id, JobStatus.IMPORTED)
            self.store.append_log(
                job_id,
                f"Belge boyut ve SHA-256 doğrulamasıyla içe aktarıldı: {imported.path.name}",
            )
        except (OSError, RuntimeError, ValueError) as finish_error:
            try:
                record = self.store.get_job(job_id)
                if record.status is JobStatus.RUNNING:
                    self.store.transition_job(
                        job_id, JobStatus.FAILED, error=str(finish_error)
                    )
            except (KeyError, OSError, RuntimeError, ValueError):
                pass
            message = self._actionable_message(
                redact_sensitive_text(str(finish_error)),
                self._t("document.record_action"),
            )
            self.status.setText(message)
            self.start_button.setEnabled(self.workspace is not None)
            QMessageBox.critical(self, self._t("document.record_failed_title"), message)
            return
        self.progress.setValue(1)
        self._refresh_history()
        try:
            self._launch_document_extraction(self.store.get_job(job_id))
        except (OSError, RuntimeError, ValueError) as extraction_error:
            self._failed(str(extraction_error))

    def _launch_document_extraction(self, record: JobRecord) -> None:
        if self.workspace is None:
            raise RuntimeError("Harici SSD kullanılamıyor.")
        if record.job_kind is not JobKind.DOCUMENT:
            raise ValueError("Seçilen iş bir belge işi değil.")
        job_directory = Path(record.job_directory).expanduser().resolve()
        if not job_directory.is_relative_to(self.workspace.jobs.resolve()):
            raise RuntimeError("Belge işi doğrulanmış SSD çalışma alanının dışında.")
        source_directory = job_directory / "source"
        if (
            source_directory.is_symlink()
            or not source_directory.is_dir()
            or not source_directory.resolve().is_relative_to(job_directory)
        ):
            raise RuntimeError("Belge kaynak klasörü güvenli değil.")
        candidates = [
            item
            for item in source_directory.glob("original.*")
            if item.is_file()
            and not item.is_symlink()
            and item.resolve().is_relative_to(source_directory.resolve())
            and item.suffix.casefold() in DOCUMENT_SUFFIXES
        ]
        if len(candidates) != 1:
            raise RuntimeError("İçe aktarılmış tek bir kaynak belge bulunamadı.")
        manifest_path = job_directory / "manifest.json"
        if (
            manifest_path.is_symlink()
            or not manifest_path.is_file()
            or not manifest_path.resolve().is_relative_to(job_directory)
        ):
            raise RuntimeError("Belge içe aktarma manifesti güvenli değil.")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            imported = manifest["import"]
            expected_sha256 = str(imported["sha256"])
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise RuntimeError("Belge içe aktarma manifesti okunamadı.") from error
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256.casefold()):
            raise RuntimeError("Belge manifestindeki SHA-256 geçersiz.")
        if record.status is not JobStatus.QUEUED:
            record = self.store.retry_job(record.id)
        self.store.reset_completed_stage(record.id, "document_extract")
        self.current_job_id = record.id
        self.current_job = job_directory
        self.source_media = None
        self.source_subtitle = None
        self.pending = []
        self.current_command = None
        self.cancel_requested = False
        self.waiting_for_ssd = False
        self.failure_handled = False
        self.stdout_buffer = JSONLBuffer()
        self.stderr_buffer = JSONLBuffer()
        self.log.clear()
        work_directory = job_directory / "work"
        if work_directory.exists() and (
            work_directory.is_symlink() or not work_directory.is_dir()
        ):
            raise RuntimeError("Belge çalışma klasörü güvenli değil.")
        work_directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not work_directory.resolve().is_relative_to(job_directory):
            raise RuntimeError("Belge çalışma klasörü güvenli iş alanının dışında.")
        self._queue_command(
            "document_extract",
            [
                sys.executable,
                "-m",
                "ksi_local.document_worker",
                "extract",
                str(candidates[0]),
                str(work_directory),
                "--expected-sha256",
                expected_sha256,
                "--ocr-helper",
                str(_ocr_helper_path()),
            ],
        )
        self.start_button.setEnabled(False)
        self.retry_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress.setRange(0, 0)
        self.status.setText(self._t("runtime.document_extracting"))
        self._refresh_history()
        self._run_next()

    def _document_translation_command(self, record: JobRecord) -> PendingCommand:
        if self.workspace is None:
            raise RuntimeError("Harici SSD kullanılamıyor.")
        job_directory = Path(record.job_directory).expanduser().resolve()
        if (
            record.job_kind is not JobKind.DOCUMENT
            or not job_directory.is_relative_to(self.workspace.jobs.resolve())
        ):
            raise RuntimeError("Belge çeviri işi güvenli SSD çalışma alanının dışında.")
        canonical = job_directory / "work/belge-kaynagi.jsonl"
        if (
            canonical.is_symlink()
            or not canonical.is_file()
            or not canonical.resolve().is_relative_to(job_directory)
        ):
            raise RuntimeError("Çevrilecek kanonik belge metni güvenli değil veya bulunamadı.")
        outputs = job_directory / "outputs"
        if outputs.exists() and (outputs.is_symlink() or not outputs.is_dir()):
            raise RuntimeError("Belge çıktı klasörü güvenli değil.")
        outputs.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not outputs.resolve().is_relative_to(job_directory):
            raise RuntimeError("Belge çıktı klasörü güvenli iş alanının dışında.")
        command = [
            sys.executable,
            "-m",
            "ksi_local",
            "translate-document",
            str(canonical),
            str(outputs),
            "--engine",
            self.preferences.translation_engine,
            "--source-language",
            record.source_language,
            "--ollama",
            str(tool_path("ollama")),
            "--models-directory",
            str(self.workspace.models_ollama),
            "--glossary",
            str(_bundled_glossary_path()),
            "--checkpoint",
            str(job_directory / "work/belge-ceviri.checkpoint.json"),
            "--source-title",
            Path(record.source_reference).name or "Belge",
        ]
        custom_glossary = job_directory / "source/belge-sozlugu.json"
        if custom_glossary.exists():
            if (
                custom_glossary.is_symlink()
                or not custom_glossary.is_file()
                or not custom_glossary.resolve().is_relative_to(job_directory)
            ):
                raise RuntimeError("Belgeye özel sözlük güvenli değil.")
            validate_document_glossary(custom_glossary)
            command.extend(("--custom-glossary", str(custom_glossary)))
        return PendingCommand("document_translate", command)

    def _append_document_translation_command(self, record: JobRecord) -> None:
        if not record.want_subtitle:
            return
        self.pending.append(self._document_translation_command(record))
        self.store.ensure_stages(record.id, ["document_translate"])

    def _document_summary_command(self, record: JobRecord) -> PendingCommand:
        if self.workspace is None:
            raise RuntimeError("Harici SSD kullanılamıyor.")
        job_directory = Path(record.job_directory).expanduser().resolve()
        if (
            record.job_kind is not JobKind.DOCUMENT
            or not job_directory.is_relative_to(self.workspace.jobs.resolve())
        ):
            raise RuntimeError("Belge özet işi güvenli SSD çalışma alanının dışında.")
        canonical = job_directory / "work/belge-kaynagi.jsonl"
        if (
            canonical.is_symlink()
            or not canonical.is_file()
            or not canonical.resolve().is_relative_to(job_directory)
        ):
            raise RuntimeError("Özetlenecek kanonik belge güvenli değil veya bulunamadı.")
        outputs = job_directory / "outputs"
        if outputs.exists() and (outputs.is_symlink() or not outputs.is_dir()):
            raise RuntimeError("Belge çıktı klasörü güvenli değil.")
        outputs.mkdir(parents=True, exist_ok=True, mode=0o700)
        translation = outputs / "belge-turkce.jsonl"
        translation_quality = outputs / "belge-turkce.kalite.json"
        wants_translation_source = record.document_summary_source in {"auto", "translation"}
        can_receive_translation = record.want_subtitle or (
            translation.is_file() and translation_quality.is_file()
        )
        if record.document_summary_source == "translation" and not can_receive_translation:
            raise RuntimeError(
                "Türkçe çeviriden özet seçildi; önce belge çevirisini de etkinleştirin."
            )
        command = [
            sys.executable,
            "-m",
            "ksi_local",
            "summarize-document",
            str(canonical),
            str(outputs),
            "--ollama",
            str(tool_path("ollama")),
            "--models-directory",
            str(self.workspace.models_ollama),
            "--summary-source",
            record.document_summary_source,
            "--profile",
            record.summary_profile,
            "--source-title",
            Path(record.source_reference).name or "Belge",
            "--source-reference",
            "Yerel belge",
            "--checkpoint",
            str(job_directory / "work/belge-ozeti.checkpoint.json"),
        ]
        if wants_translation_source and can_receive_translation:
            command.extend(
                (
                    "--translation",
                    str(translation),
                    "--translation-quality",
                    str(translation_quality),
                )
            )
        if record.document_summary_pdf:
            command.append("--pdf")
        return PendingCommand("document_summarize", command)

    def _append_document_summary_command(self, record: JobRecord) -> None:
        if not record.want_summary:
            return
        self.pending.append(self._document_summary_command(record))
        self.store.ensure_stages(record.id, ["document_summarize"])

    @staticmethod
    def _document_outputs_ready(record: JobRecord, stem: str) -> bool:
        output = Path(record.job_directory) / "outputs"
        names = (
            (
                "belge-turkce.jsonl",
                "belge-turkce.txt",
                "belge-turkce.md",
                "belge-turkce.docx",
                "belge-turkce.pdf",
                "belge-turkce.kalite.json",
            )
            if stem == "translation"
            else (
                "belge-ozeti.md",
                "belge-ozeti.docx",
                "belge-ozeti.kaynaklar.json",
                "belge-ozeti.kalite.json",
                *(("belge-ozeti.pdf",) if record.document_summary_pdf else ()),
            )
        )
        return all(
            (output / name).is_file() and not (output / name).is_symlink()
            for name in names
        )

    def _launch_document_remaining(self, record: JobRecord) -> None:
        if record.status is not JobStatus.QUEUED:
            record = self.store.retry_job(record.id)
        self.current_job_id = record.id
        self.current_job = Path(record.job_directory).expanduser().resolve()
        self.source_media = None
        self.source_subtitle = None
        self.pending = []
        self.current_command = None
        self.cancel_requested = False
        self.waiting_for_ssd = False
        self.failure_handled = False
        self.stdout_buffer = JSONLBuffer()
        self.stderr_buffer = JSONLBuffer()
        self.log.clear()
        if record.want_subtitle and not self._document_outputs_ready(record, "translation"):
            self.store.reset_completed_stage(record.id, "document_translate")
            self._append_document_translation_command(record)
        if record.want_summary and not self._document_outputs_ready(record, "summary"):
            self.store.reset_completed_stage(record.id, "document_summarize")
            self._append_document_summary_command(record)
        if not self.pending:
            raise RuntimeError("Belge için üretilecek eksik çıktı bulunamadı.")
        self.start_button.setEnabled(False)
        self.retry_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress.setRange(0, 0)
        self.status.setText(self._t("runtime.stage_default"))
        self._refresh_history()
        self._run_next()

    def _launch_document_translation(self, record: JobRecord) -> None:
        if record.status is not JobStatus.QUEUED:
            record = self.store.retry_job(record.id)
        self.store.reset_completed_stage(record.id, "document_translate")
        self.current_job_id = record.id
        self.current_job = Path(record.job_directory).expanduser().resolve()
        self.source_media = None
        self.source_subtitle = None
        self.pending = []
        self.current_command = None
        self.cancel_requested = False
        self.waiting_for_ssd = False
        self.failure_handled = False
        self.stdout_buffer = JSONLBuffer()
        self.stderr_buffer = JSONLBuffer()
        self.log.clear()
        self._append_document_translation_command(record)
        self.start_button.setEnabled(False)
        self.retry_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress.setRange(0, 0)
        self.status.setText(self._t("runtime.stage.document_translate"))
        self._refresh_history()
        self._run_next()

    def _launch_job(
        self,
        raw: str,
        existing_job: JobRecord | None,
        max_height: int,
        *,
        media_index: int = 1,
        preflight: PreflightResult | None = None,
        browser_session: BrowserSession | None = None,
    ) -> None:
        try:
            self._prepare_job(
                raw,
                existing_job,
                max_height=max_height,
                media_index=media_index,
                preflight=preflight,
                browser_session=browser_session,
            )
        except (OSError, RuntimeError, ValueError) as error:
            self._failed(str(error))
            return
        if browser_session is not None:
            # Authorization is deliberately one-job-only. The worker already has
            # its in-memory argument, while the GUI forgets the profile selection.
            self.browser_profile = None
            self.browser_profile_platform = None
            self.use_browser_session.setChecked(False)
            self.browser_profile_label.setText(
                "Ayrı profil bu iş için yetkilendirildi; seçim artık unutuldu."
            )
            self.udemy_access_confirmed.setChecked(False)
        self.start_button.setEnabled(False)
        self.retry_button.setEnabled(False)
        self.add_outputs_button.setEnabled(False)
        self.cancel_button.setEnabled(bool(self.pending))
        self.export_button.setEnabled(False)
        self.review_button.setEnabled(False)
        self.summary_review_button.setEnabled(False)
        self.open_dub_button.setEnabled(False)
        self.create_codex_package_button.setEnabled(False)
        self.import_codex_package_button.setEnabled(False)
        self.undo_codex_review_button.setEnabled(False)
        self.progress.setRange(0, 0)
        self.status.setText(self._t("runtime.started"))
        self._refresh_history()
        self._run_next()

    def _prepare_job(
        self,
        raw: str,
        existing_job: JobRecord | None,
        *,
        max_height: int,
        media_index: int = 1,
        preflight: PreflightResult | None = None,
        browser_session: BrowserSession | None = None,
    ) -> None:
        assert self.workspace is not None
        if existing_job is None:
            job_id = str(uuid.uuid4()).upper()
            self.current_job = self.workspace.jobs / job_id
        else:
            job_id = existing_job.id
            candidate = Path(existing_job.job_directory).expanduser().resolve()
            if not candidate.is_relative_to(self.workspace.jobs.resolve()):
                raise RuntimeError(
                    "Kayıtlı iş klasörü doğrulanmış SSD çalışma alanının dışında."
                )
            self.current_job = candidate
        self.current_job_id = job_id
        source_directory = self.current_job / "source"
        output_directory = self.current_job / "outputs"
        source_directory.mkdir(parents=True, exist_ok=True)
        output_directory.mkdir(parents=True, exist_ok=True)
        if existing_job is None:
            self.store.create_job(
                job_id=job_id,
                source=raw,
                source_language=str(self.language.currentData()),
                want_subtitle=self.want_subtitle.isChecked(),
                want_summary=self.want_summary.isChecked(),
                want_dub=self.want_dub.isChecked(),
                job_directory=self.current_job,
                download_only=self.download_only.isChecked(),
                max_height=max_height,
                media_index=media_index,
            )
            if preflight is not None:
                atomic_write_json(
                    self.current_job / "manifest.json",
                    {"schema_version": 1, "preflight": preflight.to_dict()},
                )

        self.pending = []
        self.current_command = None
        self.cancel_requested = False
        self.waiting_for_ssd = False
        self.failure_handled = False
        self.stdout_buffer = JSONLBuffer()
        self.stderr_buffer = JSONLBuffer()
        self.log.clear()
        language = self.store.get_job(job_id).source_language

        if raw.lower().startswith("https://"):
            self.source_subtitle = None
            self.source_media = self._find_downloaded_media(source_directory)
            download_stage = self.store.get_stage(job_id, "download")
            job = self.store.get_job(job_id)
            needs_video_for_dub = job.want_dub and (
                self.source_media is None
                or self.source_media.suffix.casefold() not in MEDIA_SUFFIXES
            )
            if (
                download_stage
                and download_stage.status == StageStatus.COMPLETED
                and not needs_video_for_dub
            ):
                self._append_processing_commands()
            else:
                download_command = [
                    sys.executable,
                    "-m",
                    "ksi_local",
                    "download",
                    raw,
                    "--output-directory",
                    str(source_directory),
                    "--yt-dlp",
                    str(self.workspace.yt_dlp),
                    "--deno",
                    str(self.workspace.deno),
                    "--ffmpeg",
                    str(tool_path("ffmpeg")),
                    "--ffprobe",
                    str(tool_path("ffprobe")),
                    "--max-height",
                    str(max_height),
                    "--media-index",
                    str(media_index),
                ]
                if browser_session is not None:
                    download_command.extend(
                        (
                            "--browser",
                            browser_session.browser,
                            "--browser-profile",
                            str(browser_session.profile_directory),
                        )
                    )
                if validate_source_url(raw).platform is Platform.UDEMY:
                    download_command.append("--confirm-udemy-access")
                subtitle_language = None
                available_subtitle_codes: set[str] = set()
                if not job.download_only:
                    selected_media = (
                        preflight.media_choice(media_index)
                        if preflight is not None
                        else None
                    )
                    subtitle_language = (
                        job.source_language
                        if job.source_language != AUTO_LANGUAGE
                        else (
                            selected_media.detected_language
                            if selected_media is not None
                            else (
                                preflight.detected_language
                                if preflight is not None
                                else None
                            )
                        )
                    )
                    available = (
                        (
                            *selected_media.manual_subtitles,
                            *selected_media.automatic_subtitles,
                        )
                        if selected_media is not None
                        else (
                            *preflight.manual_subtitles,
                            *preflight.automatic_subtitles,
                        )
                        if preflight is not None
                        else ()
                    )
                    available_subtitle_codes = {
                        item.casefold().split("-", 1)[0]
                        for item in available
                        if item.casefold().split("-", 1)[0]
                        in TURKISH_SOURCE_LANGUAGE_NAMES
                    }
                    if subtitle_language is None and selected_media is not None:
                        subtitle_language = next(
                            (
                                item.casefold().split("-", 1)[0]
                                for item in available
                                if item.casefold().split("-", 1)[0]
                                in TURKISH_SOURCE_LANGUAGE_NAMES
                            ),
                            None,
                        )
                    if subtitle_language is None and available_subtitle_codes:
                        subtitle_language = sorted(available_subtitle_codes)[0]
                summary_only = (
                    job.want_summary and not job.want_subtitle and not job.want_dub
                )
                has_ready_subtitle = subtitle_language in available_subtitle_codes
                download_mode = (
                    "subtitles"
                    if summary_only and has_ready_subtitle
                    else "audio"
                    if summary_only
                    else "video"
                )
                download_command.extend(("--mode", download_mode))
                if download_mode == "video" and not job.download_only:
                    download_command.append("--require-audio")
                if (
                    download_mode != "audio"
                    and subtitle_language in TURKISH_SOURCE_LANGUAGE_NAMES
                ):
                    download_command.extend(("--subtitle-language", subtitle_language))
                self._queue_command(
                    (
                        "download_video"
                        if needs_video_for_dub
                        and download_stage is not None
                        and download_stage.status == StageStatus.COMPLETED
                        else "download"
                    ),
                    download_command,
                )
        else:
            local = Path(raw).expanduser().resolve()
            if local.suffix.casefold() in TEXT_SOURCE_SUFFIXES:
                if local.suffix.casefold() == ".srt":
                    self.source_subtitle = local
                else:
                    imported = self.current_job / "work/imported-source.srt"
                    convert_text_source_to_srt(local, imported)
                    self.source_subtitle = imported
                self.source_media = None
            else:
                self.source_subtitle = self._find_sidecar(local, language)
                self.source_media = local
            self._append_processing_commands()

    def _queue_command(self, stage: str, argv: list[str]) -> None:
        assert self.current_job_id is not None
        self.store.ensure_stages(self.current_job_id, [stage])
        self.pending.append(PendingCommand(stage, argv))

    @staticmethod
    def _find_downloaded_media(source_directory: Path) -> Path | None:
        candidates = [
            item
            for item in source_directory.iterdir()
            if item.is_file()
            and item.name.startswith("source")
            and item.suffix.casefold() in PROCESSING_MEDIA_SUFFIXES
            and not item.name.endswith(".part")
        ]
        return sorted(candidates)[0] if candidates else None

    @staticmethod
    def _find_downloaded_video(source_directory: Path) -> Path | None:
        candidates = [
            item
            for item in source_directory.iterdir()
            if item.is_file()
            and item.name.startswith("source")
            and item.suffix.casefold() in MEDIA_SUFFIXES
            and not item.name.endswith(".part")
        ]
        return sorted(candidates)[0] if candidates else None

    def _stage_output_is_complete(self, stage: str, output: Path) -> bool:
        assert self.current_job_id is not None
        record = self.store.get_stage(self.current_job_id, stage)
        if record is None or record.status != StageStatus.COMPLETED:
            return False
        if output.is_file():
            return True
        raise RuntimeError(
            f"{stage} aşaması tamamlanmış görünüyor ancak çıktı dosyası SSD'de yok."
        )

    def _append_processing_commands(self) -> None:
        assert (
            self.workspace is not None
            and self.current_job is not None
            and self.current_job_id is not None
        )
        job = self.store.get_job(self.current_job_id)
        if job.download_only:
            return
        if job.want_dub and (
            self.source_media is None
            or self.source_media.suffix.casefold() not in MEDIA_SUFFIXES
        ):
            self.source_media = self._find_downloaded_video(self.current_job / "source")
            if self.source_media is None:
                raise RuntimeError("Türkçe dublaj için kaynak video bulunamadı.")
        language = job.source_language
        if self.source_subtitle is None:
            source_directory = self.current_job / "source"
            ready = discover_best_subtitle(
                source_directory,
                requested_language=language,
                stem_prefix="source",
            )
            if ready is not None:
                self.source_subtitle = ready.path
                message = (
                    f"Hazır {turkish_language_name(ready.detected_language)} altyazı "
                    "kullanılacak; Whisper atlandı."
                )
                self.log.appendPlainText(message)
                self.store.append_log(self.current_job_id, message)
            else:
                if self.source_media is None:
                    self.source_media = self._find_downloaded_media(source_directory)
                if self.source_media is None:
                    raise RuntimeError("İndirilen veya seçilen medya dosyası bulunamadı.")
                self.source_subtitle = self.current_job / "work" / f"transcript.{language}.srt"
                self.source_subtitle.parent.mkdir(parents=True, exist_ok=True)
                if not self._stage_output_is_complete("transcribe", self.source_subtitle):
                    self._queue_command(
                        "transcribe",
                        [
                            sys.executable,
                            "-m",
                            "ksi_local",
                            "transcribe",
                            str(self.source_media),
                            str(self.source_subtitle),
                            "--source-language",
                            language,
                            "--model",
                            str(self.workspace.models_whisper),
                            "--glossary",
                            str(_bundled_glossary_path()),
                        ],
                    )

        output_directory = self.current_job / "outputs"
        common = [
            "--ollama",
            str(tool_path("ollama")),
            "--models-directory",
            str(self.workspace.models_ollama),
        ]
        translated_subtitle = output_directory / "turkce.srt"
        quality_report = output_directory / "turkce.kalite.json"
        if (job.want_subtitle or job.want_dub) and not self._stage_output_is_complete(
            "translate", translated_subtitle
        ):
            self._queue_command(
                "translate",
                [
                    sys.executable,
                    "-m",
                    "ksi_local",
                    "translate-srt",
                    str(self.source_subtitle),
                    str(translated_subtitle),
                    "--engine",
                    self.preferences.translation_engine,
                    "--source-language",
                    language,
                    "--glossary",
                    str(_bundled_glossary_path()),
                    "--quality-report",
                    str(quality_report),
                    *common,
                ],
            )

        if job.want_subtitle:
            if (
                self.source_media is None
                or self.source_media.suffix.casefold() not in MEDIA_SUFFIXES
            ):
                self.source_media = self._find_downloaded_video(
                    self.current_job / "source"
                )
            if self.source_media is not None:
                subtitled_video = subtitled_output_path(
                    self.source_media, output_directory
                )
                if not self._stage_output_is_complete("subtitle_mux", subtitled_video):
                    self._queue_command(
                        "subtitle_mux",
                        [
                            sys.executable,
                            "-m",
                            "ksi_local",
                            "mux-subtitle",
                            str(self.source_media),
                            str(translated_subtitle),
                            str(subtitled_video),
                            "--ffmpeg",
                            str(tool_path("ffmpeg")),
                            "--ffprobe",
                            str(tool_path("ffprobe")),
                        ],
                    )

        if job.want_dub:
            assert self.source_media is not None
            dubbed_audio = output_directory / "turkce-dublaj.wav"
            timing_report = output_directory / "turkce-dublaj.zamanlama.json"
            segment_directory = self.current_job / "work/dub-segments"
            ffmpeg = str(tool_path("ffmpeg"))
            ffprobe = str(tool_path("ffprobe"))
            recognized = self.current_job / "work/turkce-dublaj.asr.srt"
            dub_quality = output_directory / "turkce-dublaj.kalite.json"
            dubbed_video = output_directory / "turkce-dublaj.mp4"
            final_dub_ready = self._stage_output_is_complete("mux", dubbed_video)
            if not final_dub_ready:
                cpu_voice = host_architecture() == "x86_64"
                chatterbox_python = Path(sys.executable) if cpu_voice else _chatterbox_python_path()
                voice_profile = _bundled_voice_profile_path()
                tts_model = self.workspace.root / ("models/tts/piper" if cpu_voice else "models/tts/chatterbox-multilingual-v3")
                if not chatterbox_python.is_file():
                    raise RuntimeError("Chatterbox ARM64 çalışma ortamı bulunamadı.")
                if not voice_profile.is_file() or not tts_model.is_dir():
                    raise RuntimeError(
                        "Kabul edilen dublaj ses profili veya modeli bulunamadı."
                    )
                if not self._stage_output_is_complete("tts", dubbed_audio):
                    self._queue_command(
                        "tts",
                        [
                            str(chatterbox_python),
                            "-m",
                            "ksi_local.tts_worker",
                            str(translated_subtitle),
                            str(dubbed_audio),
                            "--engine",
                            "piper" if cpu_voice else "chatterbox",
                            "--segments-directory",
                            str(segment_directory),
                            "--model-directory",
                            str(tts_model),
                            "--voice-profile",
                            str(voice_profile),
                            "--ffmpeg",
                            ffmpeg,
                            "--report",
                            str(timing_report),
                        ],
                    )
                if not self._stage_output_is_complete("dub_quality", dub_quality):
                    self._queue_command(
                        "dub_quality",
                        [
                            sys.executable,
                            "-m",
                            "ksi_local",
                            "quality-dub",
                            str(translated_subtitle),
                            str(dubbed_audio),
                            str(recognized),
                            "--timing-report",
                            str(timing_report),
                            "--output",
                            str(dub_quality),
                            "--model",
                            str(self.workspace.models_whisper),
                        ],
                    )
                self._queue_command(
                    "mux",
                    [
                        sys.executable,
                        "-m",
                        "ksi_local",
                        "mux-dub",
                        str(self.source_media),
                        str(dubbed_audio),
                        str(dubbed_video),
                        "--ffmpeg",
                        ffmpeg,
                        "--ffprobe",
                        ffprobe,
                        "--original-volume",
                        "0.12",
                        "--quality-report",
                        str(dub_quality),
                    ],
                )
        summary = output_directory / "ozet.md"
        summary_trace = output_directory / "ozet.kaynaklar.json"
        summary_quality = output_directory / "ozet.kalite.json"
        if job.want_summary and not self._stage_output_is_complete(
            "summarize", summary
        ):
            self._queue_command(
                "summarize",
                [
                    sys.executable,
                    "-m",
                    "ksi_local",
                    "summarize-srt",
                    str(self.source_subtitle),
                    str(summary),
                    "--source-title",
                    self._summary_source_title(self.current_job, job),
                    "--source-reference",
                    job.source_reference,
                    "--trace-report",
                    str(summary_trace),
                    "--quality-report",
                    str(summary_quality),
                    *common,
                ],
            )

    def _run_next(self) -> None:
        if not self.pending:
            self._completed()
            return
        command = self.pending.pop(0)
        self.current_command = command
        assert self.current_job_id is not None
        job = self.store.get_job(self.current_job_id)
        if job.status == JobStatus.QUEUED:
            self.store.transition_job(
                self.current_job_id,
                JobStatus.RUNNING,
                current_stage=command.stage,
            )
        self.store.set_stage(self.current_job_id, command.stage, StageStatus.RUNNING)
        stage_key = f"runtime.stage.{command.stage}"
        stage_text = self._t(stage_key)
        self.status.setText(
            self._t("runtime.stage_default") if stage_text == stage_key else stage_text
        )
        self.process = QProcess(self)
        environment = QProcessEnvironment.systemEnvironment()
        project_src = str(Path(__file__).resolve().parents[1])
        environment.insert("PYTHONPATH", project_src)
        environment.insert("KSI_WORKER", "1")
        self.process.setProcessEnvironment(environment)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.readyReadStandardError.connect(self._read_error)
        self.process.finished.connect(self._process_finished)
        self.process.errorOccurred.connect(self._process_error)
        self.cancel_button.setEnabled(True)
        self.log.appendPlainText(f"→ {command.stage}")
        self.store.append_log(
            self.current_job_id,
            self._t("runtime.stage_started", stage=command.stage),
        )
        self._refresh_history()
        self.process.start(command.argv[0], command.argv[1:])

    def _read_output(self) -> None:
        assert self.process is not None
        output = bytes(self.process.readAllStandardOutput()).decode(errors="replace")
        for line in self.stdout_buffer.feed(output):
            self._handle_stdout_line(line)

    def _handle_stdout_line(self, line: str) -> None:
        safe_line = redact_sensitive_text(line).strip()
        if not safe_line:
            return
        if self.cancel_requested or self.waiting_for_ssd or self.failure_handled:
            return
        try:
            event = parse_worker_event(safe_line)
        except ValueError:
            self.log.appendPlainText(safe_line)
            if self.current_job_id:
                self.store.append_log(self.current_job_id, safe_line)
            return
        if self.current_command is None or event.stage != self.current_command.stage:
            message = self._t(
                "runtime.unexpected_worker", stage=event.stage, event=event.event
            )
            self.log.appendPlainText(message)
            if self.current_job_id:
                self.store.append_log(self.current_job_id, message, level="warning")
            return
        assert self.current_job_id is not None
        if event.event == "progress":
            assert event.completed is not None and event.total is not None
            self.store.update_stage_progress(
                self.current_job_id, event.stage, event.completed, event.total
            )
            self.progress.setRange(0, event.total)
            self.progress.setValue(event.completed)
            self.status.setText(
                f"{self.status.text().split(' — ')[0]} — {event.completed}/{event.total}"
            )
        elif event.event == "language_detected":
            payload = event.payload or {}
            label = str(
                payload.get("label")
                or payload.get("language")
                or self._t("runtime.language_unknown")
            )
            confidence = payload.get("confidence")
            suffix = (
                self._t(
                    "runtime.confidence_suffix",
                    confidence=float(confidence) * 100,
                )
                if isinstance(confidence, (int, float))
                else ""
            )
            self.status.setText(
                self._t("runtime.source_detected", language=label, suffix=suffix)
            )
        elif event.event == "completed":
            self.store.set_stage(self.current_job_id, event.stage, StageStatus.COMPLETED)
            self.log.appendPlainText(f"✓ {event.stage}")
            if event.stage == "translate":
                payload = event.payload or {}
                errors = int(payload.get("quality_errors") or 0)
                warnings = int(payload.get("quality_warnings") or 0)
                if errors or warnings:
                    self.log.appendPlainText(
                        self._t(
                            "runtime.subtitle_quality", errors=errors, warnings=warnings
                        )
                    )
                else:
                    self.log.appendPlainText(self._t("runtime.subtitle_passed"))
            elif event.stage == "subtitle_mux":
                payload = event.payload or {}
                duration = float(payload.get("duration_seconds") or 0)
                suffix = (
                    self._t("runtime.duration_suffix", minutes=duration / 60)
                    if duration
                    else "."
                )
                self.log.appendPlainText(
                    self._t("runtime.subtitle_mux_verified", suffix=suffix)
                )
            elif event.stage == "summarize":
                payload = event.payload or {}
                statements = int(payload.get("statement_count") or 0)
                coverage = float(payload.get("source_coverage_percent") or 0)
                self.log.appendPlainText(
                    self._t(
                        "runtime.summary_quality",
                        statements=statements,
                        coverage=coverage,
                    )
                )
            elif event.stage == "tts":
                payload = event.payload or {}
                normal = float(payload.get("normal_speed_percent") or 0)
                rewrites = int(payload.get("needs_rewrite_count") or 0)
                restored = int(payload.get("restored_segment_count") or 0)
                self.log.appendPlainText(
                    self._t(
                        "runtime.dub_timing",
                        normal=normal,
                        rewrites=rewrites,
                        restored=restored,
                    )
                )
            elif event.stage == "dub_quality":
                payload = event.payload or {}
                cer = float(payload.get("character_error_percent") or 0)
                state = self._t(
                    "runtime.quality_passed"
                    if payload.get("quality_passed")
                    else "runtime.quality_review"
                )
                self.log.appendPlainText(
                    self._t("runtime.dub_quality", cer=cer, state=state)
                )
            elif event.stage == "document_extract":
                payload = event.payload or {}
                blocks = int(payload.get("block_count") or 0)
                characters = int(payload.get("character_count") or 0)
                ocr_blocks = int(payload.get("ocr_block_count") or 0)
                low_confidence = int(
                    payload.get("low_ocr_confidence_block_count") or 0
                )
                self.log.appendPlainText(
                    self._t(
                        "runtime.document_extracted",
                        blocks=blocks,
                        characters=characters,
                        ocr=ocr_blocks,
                        low=low_confidence,
                    )
                )
                if self.current_job is not None:
                    manifest_path = self.current_job / "manifest.json"
                    try:
                        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                        if not isinstance(manifest, dict):
                            raise ValueError("Belge manifesti nesne biçiminde değil.")
                        manifest["schema_version"] = max(
                            int(manifest.get("schema_version") or 1), 3
                        )
                        manifest["extraction"] = {
                            "source_sha256": str(payload.get("source_sha256") or ""),
                            "block_count": blocks,
                            "character_count": characters,
                            "ocr_block_count": ocr_blocks,
                            "low_ocr_confidence_block_count": low_confidence,
                            "jsonl": "work/belge-kaynagi.jsonl",
                            "text": "work/belge-kaynagi.txt",
                            "quality": "work/belge-kaynagi.kalite.json",
                        }
                        atomic_write_json(manifest_path, manifest)
                    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
                        self._failed(f"Belge çıkarma manifesti yazılamadı: {error}")
            elif event.stage == "document_translate":
                payload = event.payload or {}
                translated = int(payload.get("translated_block_count") or 0)
                preserved = int(payload.get("preserved_block_count") or 0)
                errors = int(payload.get("quality_errors") or 0)
                warnings = int(payload.get("quality_warnings") or 0)
                self.log.appendPlainText(
                    self._t(
                        "runtime.document_translated",
                        translated=translated,
                        preserved=preserved,
                        errors=errors,
                        warnings=warnings,
                    )
                )
                if self.current_job is not None:
                    manifest_path = self.current_job / "manifest.json"
                    try:
                        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                        if not isinstance(manifest, dict):
                            raise ValueError("Belge manifesti nesne biçiminde değil.")
                        manifest["schema_version"] = max(
                            int(manifest.get("schema_version") or 1), 4
                        )
                        manifest["translation"] = {
                            "block_count": int(payload.get("block_count") or 0),
                            "translated_block_count": translated,
                            "preserved_block_count": preserved,
                            "language_counts": payload.get("language_counts") or {},
                            "quality_passed": bool(payload.get("quality_passed")),
                            "quality_errors": errors,
                            "quality_warnings": warnings,
                            "jsonl": "outputs/belge-turkce.jsonl",
                            "text": "outputs/belge-turkce.txt",
                            "markdown": "outputs/belge-turkce.md",
                            "docx": "outputs/belge-turkce.docx",
                            "pdf": "outputs/belge-turkce.pdf",
                            "quality": "outputs/belge-turkce.kalite.json",
                            "checkpoint": "work/belge-ceviri.checkpoint.json",
                        }
                        atomic_write_json(manifest_path, manifest)
                    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
                        self._failed(f"Belge çeviri manifesti yazılamadı: {error}")
            elif event.stage == "document_summarize":
                payload = event.payload or {}
                statements = int(payload.get("statement_count") or 0)
                claims = int(payload.get("claim_count") or 0)
                source_coverage = float(payload.get("source_coverage_percent") or 0)
                evidence_coverage = float(payload.get("evidence_coverage_percent") or 0)
                self.log.appendPlainText(
                    self._t(
                        "runtime.document_summarized",
                        claims=claims,
                        statements=statements,
                        source=source_coverage,
                        evidence=evidence_coverage,
                    )
                )
                if self.current_job is not None:
                    manifest_path = self.current_job / "manifest.json"
                    try:
                        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                        if not isinstance(manifest, dict):
                            raise ValueError("Belge manifesti nesne biçiminde değil.")
                        manifest["schema_version"] = max(
                            int(manifest.get("schema_version") or 1), 5
                        )
                        manifest["summary"] = {
                            "profile": str(payload.get("profile") or "standard"),
                            "source_mode": str(payload.get("source_mode") or "source"),
                            "chunk_count": int(payload.get("chunk_count") or 0),
                            "claim_count": claims,
                            "statement_count": statements,
                            "source_coverage_percent": source_coverage,
                            "evidence_coverage_percent": evidence_coverage,
                            "quality_passed": bool(payload.get("quality_passed")),
                            "markdown": "outputs/belge-ozeti.md",
                            "docx": "outputs/belge-ozeti.docx",
                            "pdf": (
                                "outputs/belge-ozeti.pdf" if payload.get("pdf") else None
                            ),
                            "trace": "outputs/belge-ozeti.kaynaklar.json",
                            "quality": "outputs/belge-ozeti.kalite.json",
                            "checkpoint": "work/belge-ozeti.checkpoint.json",
                        }
                        atomic_write_json(manifest_path, manifest)
                    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
                        self._failed(f"Belge özet manifesti yazılamadı: {error}")
        elif event.event == "error":
            self._failed(event.message or self._t("runtime.worker_unknown"))
        elif event.message:
            self.log.appendPlainText(event.message)
            self.store.append_log(self.current_job_id, event.message)

    def _read_error(self) -> None:
        assert self.process is not None
        output = bytes(self.process.readAllStandardError()).decode(errors="replace")
        for line in self.stderr_buffer.feed(output):
            self._append_error_line(line)

    def _append_error_line(self, line: str) -> None:
        safe_line = redact_sensitive_text(line).strip()
        if safe_line:
            self.log.appendPlainText(safe_line)
            if self.current_job_id:
                self.store.append_log(self.current_job_id, safe_line, level="error")

    def _process_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart and not self.cancel_requested:
            self._failed(self._t("runtime.process_start_failed"))
            if self.process is not None:
                self.process.deleteLater()
                self.process = None
                self.start_button.setEnabled(self.workspace is not None)

    def _process_finished(self, exit_code: int, _status: QProcess.ExitStatus) -> None:
        for line in self.stdout_buffer.flush():
            self._handle_stdout_line(line)
        for line in self.stderr_buffer.flush():
            self._append_error_line(line)
        finished_command = self.current_command
        process = self.process
        self.process = None
        if process is not None:
            process.deleteLater()
        if self.failure_handled:
            self.current_command = None
            self.cancel_button.setEnabled(False)
            self.start_button.setEnabled(self.workspace is not None)
            self._refresh_history()
            return
        if self.waiting_for_ssd:
            self.current_command = None
            self.cancel_button.setEnabled(False)
            self._refresh_history()
            return
        if self.cancel_requested:
            self.current_command = None
            self.cancel_button.setEnabled(False)
            self.progress.setRange(0, 1)
            self.progress.setValue(0)
            self.status.setText(self._t("runtime.cancelled"))
            self._refresh_history()
            return
        if exit_code != 0:
            self._failed(self._t("runtime.process_failed_code", code=exit_code))
            return
        if finished_command is not None and self.current_job_id is not None:
            self.store.set_stage(
                self.current_job_id, finished_command.stage, StageStatus.COMPLETED
            )
        self.current_command = None
        if finished_command is not None and finished_command.stage in {
            "download",
            "download_video",
        }:
            try:
                assert self.current_job is not None
                if finished_command.stage == "download_video":
                    self.source_media = self._find_downloaded_video(
                        self.current_job / "source"
                    )
                else:
                    self.source_media = self._find_downloaded_media(
                        self.current_job / "source"
                    )
                self._append_processing_commands()
            except RuntimeError as error:
                self._failed(str(error))
                return
        if (
            finished_command is not None
            and finished_command.stage == "document_extract"
            and self.current_job_id is not None
        ):
            try:
                self._append_document_translation_command(
                    self.store.get_job(self.current_job_id)
                )
                self._append_document_summary_command(
                    self.store.get_job(self.current_job_id)
                )
            except (OSError, RuntimeError, ValueError) as error:
                self._failed(str(error))
                return
        self._run_next()

    def _failed(self, message: str, *, show_dialog: bool = True) -> None:
        diagnostic = redact_sensitive_text(message).strip()
        safe_message = self._actionable_message(
            self._t(_core_error_key(diagnostic)),
            self._t("error.retry"),
        )
        self.failure_handled = True
        if self.current_job_id:
            try:
                job = self.store.get_job(self.current_job_id)
            except KeyError:
                job = None
            if job is not None:
                if self.current_command:
                    stage = self.store.get_stage(self.current_job_id, self.current_command.stage)
                    if stage and stage.status == StageStatus.RUNNING:
                        self.store.set_stage(
                            self.current_job_id,
                            self.current_command.stage,
                            StageStatus.FAILED,
                            error=diagnostic,
                        )
                if job.status == JobStatus.QUEUED:
                    self.store.transition_job(self.current_job_id, JobStatus.RUNNING)
                    job = self.store.get_job(self.current_job_id)
                if job.status == JobStatus.RUNNING:
                    self.store.transition_job(
                        self.current_job_id, JobStatus.FAILED, error=diagnostic
                    )
        self.pending.clear()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.status.setText(safe_message)
        if diagnostic:
            self.log.appendPlainText(diagnostic)
            if self.current_job_id:
                self.store.append_log(self.current_job_id, diagnostic, level="error")
        self.start_button.setEnabled(self.workspace is not None and not self._process_is_running())
        self.cancel_button.setEnabled(False)
        self._refresh_history()
        if self.process is not None and self.process.state() != QProcess.ProcessState.NotRunning:
            self._terminate_process(self.process)
        if show_dialog:
            QMessageBox.critical(self, self._t("error.failed_title"), safe_message)

    def _completed(self) -> None:
        if self.current_job_id:
            job = self.store.get_job(self.current_job_id)
            if job.status == JobStatus.QUEUED:
                self.store.transition_job(self.current_job_id, JobStatus.RUNNING)
                job = self.store.get_job(self.current_job_id)
            if job.status == JobStatus.RUNNING:
                if job.job_kind is JobKind.DOCUMENT:
                    translation_path = Path(job.job_directory) / "outputs/belge-turkce.jsonl"
                    document_summary_path = (
                        Path(job.job_directory) / "outputs/belge-ozeti.md"
                    )
                    target_status = (
                        JobStatus.COMPLETED
                        if (
                            document_summary_path.is_file()
                            and not document_summary_path.is_symlink()
                        )
                        or (
                            translation_path.is_file()
                            and not translation_path.is_symlink()
                        )
                        else JobStatus.EXTRACTED
                    )
                else:
                    target_status = JobStatus.COMPLETED
                self.store.transition_job(self.current_job_id, target_status)
                job = self.store.get_job(self.current_job_id)
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        translated = (
            self.current_job / "outputs/turkce.srt"
            if self.current_job is not None
            else None
        )
        summary = (
            self.current_job / "outputs/ozet.md"
            if self.current_job is not None
            else None
        )
        dubbed = (
            self.current_job / "outputs/turkce-dublaj.mp4"
            if self.current_job is not None
            else None
        )
        subtitled = (
            next(
                (
                    path
                    for path in (
                        self.current_job / "outputs/turkce-altyazili.mp4",
                        self.current_job / "outputs/turkce-altyazili.mkv",
                    )
                    if path.is_file()
                ),
                None,
            )
            if self.current_job is not None
            else None
        )
        is_document_job = bool(
            self.current_job_id
            and self.store.get_job(self.current_job_id).job_kind is JobKind.DOCUMENT
        )
        if is_document_job:
            document_summary_quality_path = (
                self.current_job / "outputs/belge-ozeti.kalite.json"
                if self.current_job is not None
                else None
            )
            translation_quality_path = (
                self.current_job / "outputs/belge-turkce.kalite.json"
                if self.current_job is not None
                else None
            )
            extraction_quality_path = (
                self.current_job / "work/belge-kaynagi.kalite.json"
                if self.current_job is not None
                else None
            )
            try:
                summary_quality = (
                    json.loads(document_summary_quality_path.read_text(encoding="utf-8"))
                    if document_summary_quality_path
                    and document_summary_quality_path.is_file()
                    else {}
                )
            except (OSError, json.JSONDecodeError):
                summary_quality = {}
            try:
                quality = (
                    json.loads(translation_quality_path.read_text(encoding="utf-8"))
                    if translation_quality_path and translation_quality_path.is_file()
                    else {}
                )
            except (OSError, json.JSONDecodeError):
                quality = {}
            if isinstance(summary_quality, dict) and summary_quality:
                message = self._t(
                    "runtime.completed_document_summary",
                    statements=int(summary_quality.get("statement_count") or 0),
                    coverage=float(summary_quality.get("source_coverage_percent") or 0),
                )
            elif isinstance(quality, dict) and quality:
                message = self._t(
                    "runtime.completed_document_translation",
                    translated=int(quality.get("translated_block_count") or 0),
                    preserved=int(quality.get("preserved_block_count") or 0),
                    warnings=int(quality.get("warning_count") or 0),
                )
            else:
                try:
                    extraction_quality = (
                        json.loads(extraction_quality_path.read_text(encoding="utf-8"))
                        if extraction_quality_path
                        else {}
                    )
                except (OSError, json.JSONDecodeError):
                    extraction_quality = {}
                blocks = int(extraction_quality.get("block_count") or 0)
                low_ocr = int(
                    extraction_quality.get("low_ocr_confidence_block_count") or 0
                )
                message = self._t(
                    "runtime.completed_document_ocr"
                    if low_ocr
                    else "runtime.completed_document_text",
                    blocks=blocks,
                    low=low_ocr,
                )
        elif dubbed is not None and dubbed.is_file() and subtitled is not None:
            message = self._t("runtime.completed_dub_and_subtitles")
        elif dubbed is not None and dubbed.is_file():
            message = self._t("runtime.completed_dub")
        elif subtitled is not None:
            message = self._t("runtime.completed_subtitled_video")
        elif (
            translated is not None
            and translated.is_file()
            and summary is not None
            and summary.is_file()
        ):
            message = self._t("runtime.completed_texts")
        elif translated is not None and translated.is_file():
            message = self._t("runtime.completed_subtitle")
        elif summary is not None and summary.is_file():
            message = self._t("runtime.completed_summary")
        else:
            message = self._t("runtime.completed")
        self.status.setText(message)
        self.start_button.setEnabled(self.workspace is not None)
        self.cancel_button.setEnabled(False)
        self._refresh_history()

    def _cancel(self) -> None:
        controller = getattr(self, "tool_controller", None)
        if controller is not None and controller.busy:
            controller.cancel()
            return
        if (
            self.document_import_thread is not None
            and self.document_import_thread.is_alive()
            and self.maintenance_pending
        ):
            self.document_import_cancel.set()
            self.status.setText(self._t("runtime.import_cancelling"))
            self.cancel_button.setEnabled(False)
            return
        if not self.current_job_id:
            return
        self.cancel_requested = True
        self.pending.clear()
        if self.current_command:
            stage = self.store.get_stage(self.current_job_id, self.current_command.stage)
            if stage and stage.status == StageStatus.RUNNING:
                self.store.set_stage(
                    self.current_job_id,
                    self.current_command.stage,
                    StageStatus.CANCELLED,
                )
        job = self.store.get_job(self.current_job_id)
        if job.status not in {JobStatus.CANCELLED, JobStatus.COMPLETED}:
            self.core.stop(self.current_job_id, confirm=True)
        self.status.setText(self._t("runtime.cancelling"))
        self.cancel_button.setEnabled(False)
        if self._process_is_running():
            assert self.process is not None
            process = self.process
            self._terminate_process(process)
            QTimer.singleShot(3000, lambda: self._kill_if_running(process))
        else:
            self._refresh_history()

    def _export_kind_changed(self) -> None:
        selected = str(self.export_kind.currentData() or "all")
        self.preferences = replace(self.preferences, export_kind=selected)
        try:
            save_preferences(self.preferences)
        except OSError:
            pass

    def _export_target_changed(self) -> None:
        self.export_directory_button.setEnabled(
            self.export_target.currentData() == "custom"
        )

    def _select_export_directory(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            self._t("export.choose_target"),
            str(self.custom_export_directory or Path.home()),
        )
        if selected:
            self.custom_export_directory = Path(selected).expanduser().resolve()
            self.export_directory_button.setText(
                self.custom_export_directory.name[:24] or self._t("export.selected")
            )

    @staticmethod
    def _documents_path() -> Path:
        resolved = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DocumentsLocation
        )
        return Path(resolved or (Path.home() / "Documents")).expanduser().resolve()

    def _export_destination(self) -> Path:
        selected = str(self.export_target.currentData() or "desktop")
        if selected == "documents":
            return self._documents_path()
        if selected == "custom":
            if self.custom_export_directory is None:
                raise ValueError("Önce Mac'te bir hedef klasör seçin.")
            return self.custom_export_directory
        return self._desktop_path()

    @staticmethod
    def _desktop_path() -> Path:
        resolved = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.DesktopLocation
        )
        return Path(resolved or (Path.home() / "Desktop")).expanduser().resolve()

    def _export(self) -> None:
        record = self._selected_record()
        if record is None or self.maintenance_pending:
            return
        if record.status is not JobStatus.COMPLETED:
            QMessageBox.warning(
                self,
                self._t("export.not_ready_title"),
                self._actionable_message(
                    self._t("export.not_complete"),
                    self._t("export.not_complete_action"),
                ),
            )
            return
        job_directory = Path(record.job_directory)
        if not self._job_directory_is_valid(job_directory):
            QMessageBox.critical(
                self,
                self._t("export.failed_title"),
                self._actionable_message(
                    self._t("export.outside_workspace"),
                    self._t("export.workspace_action"),
                ),
            )
            return
        try:
            title = self._summary_source_title(job_directory, record)
            if record.source_kind == "file":
                title = Path(record.source_reference).stem
            artifacts = select_job_artifacts(
                job_directory,
                export_kind=str(self.export_kind.currentData() or "all"),
                title=title,
            )
            desktop = self._export_destination()
            total_bytes = export_total_bytes(artifacts)
        except (OSError, ValueError) as error:
            QMessageBox.critical(
                self,
                self._t("export.failed_title"),
                self._actionable_message(
                    str(error), self._t("export.target_action")
                ),
            )
            return
        if (
            total_bytes >= ICLOUD_LARGE_EXPORT_BYTES
            and desktop_uses_icloud(desktop)
            and not self.preferences.icloud_warning_acknowledged
        ):
            answer = QMessageBox.question(
                self,
                self._t("export.icloud_title"),
                self._t("export.icloud_body"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            self.preferences = replace(
                self.preferences, icloud_warning_acknowledged=True
            )
            try:
                save_preferences(self.preferences)
            except OSError:
                pass
        folder_name = safe_filename(f"{title} [{record.id[:8]}]")
        self.maintenance_pending = True
        self.start_button.setEnabled(False)
        self.status.setText(self._t("export.copying"))
        self._refresh_history()
        self.export_thread = threading.Thread(
            target=self._export_in_background,
            args=(artifacts, desktop, folder_name),
            name="KSI Local Studio-Mac-Kopya",
            daemon=True,
        )
        self.export_thread.start()

    def _export_in_background(
        self,
        artifacts: list[ExportArtifact],
        desktop: Path,
        folder_name: str,
    ) -> None:
        try:
            destination = export_artifacts(
                artifacts,
                desktop=desktop,
                folder_name=folder_name,
            )
        except (OSError, RuntimeError, ValueError) as error:
            destination = None
            export_error: RuntimeError | None = RuntimeError(
                redact_sensitive_text(str(error))
            )
        else:
            export_error = None
        try:
            self.export_bridge.finished.emit(destination, export_error)
        except RuntimeError:
            return

    def _export_finished(
        self, destination: Path | None, error: RuntimeError | None
    ) -> None:
        self.maintenance_pending = False
        self.start_button.setEnabled(self.workspace is not None)
        self._refresh_history()
        if destination is None:
            message = self._actionable_message(
                str(error or self._t("export.incomplete")),
                self._t("export.retry_action"),
            )
            self.status.setText(message)
            QMessageBox.critical(
                self,
                self._t("export.failed_title"),
                message
                + "\n\n"
                + self._t("export.source_preserved"),
            )
            return
        self.preferences = replace(
            self.preferences, last_export_directory=str(destination.resolve())
        )
        try:
            save_preferences(self.preferences)
        except OSError:
            pass
        self.status.setText(self._t("export.copied_status", name=destination.name))
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(destination)))
        QMessageBox.information(
            self,
            self._t("export.copied_title"),
            self._t("export.copied_body", destination=destination),
        )

    def _cleanup_selected(self) -> None:
        record = self._selected_record()
        if record is None or self.maintenance_pending:
            return
        job_directory = Path(record.job_directory)
        if (
            record.status is not JobStatus.COMPLETED
            or not self._job_directory_is_valid(job_directory)
        ):
            QMessageBox.warning(
                self,
                self._t("cleanup.not_ready_title"),
                self._actionable_message(
                    self._t("cleanup.not_verified"),
                    self._t("cleanup.select_action"),
                ),
            )
            return
        try:
            inventory = cleanup_inventory(job_directory)
        except (OSError, ValueError) as error:
            QMessageBox.critical(
                self,
                self._t("cleanup.inspect_failed_title"),
                self._actionable_message(
                    str(error), self._t("cleanup.retry_action")
                ),
            )
            return
        if not inventory:
            QMessageBox.information(
                self, self._t("cleanup.none_title"), self._t("cleanup.none_body")
            )
            return
        total = sum(item.size_bytes for item in inventory)
        preview = "\n".join(f"• {item.relative_path}" for item in inventory[:8])
        if len(inventory) > 8:
            preview += "\n" + self._t("cleanup.more", count=len(inventory) - 8)
        answer = QMessageBox.question(
            self,
            self._t("cleanup.confirm_title"),
            self._t(
                "cleanup.confirm_body",
                count=len(inventory),
                size=_human_bytes(total),
                preview=preview,
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        approved = tuple(item.relative_path for item in inventory)
        self.maintenance_pending = True
        self.start_button.setEnabled(False)
        self.status.setText(self._t("cleanup.running"))
        self._refresh_history()
        self.cleanup_thread = threading.Thread(
            target=self._cleanup_in_background,
            args=(job_directory, approved),
            name="KSI Local Studio-SSD-Temizlik",
            daemon=True,
        )
        self.cleanup_thread.start()

    def _cleanup_in_background(
        self, job_directory: Path, approved: tuple[str, ...]
    ) -> None:
        try:
            result = cleanup_intermediates(
                job_directory,
                expected_relative_paths=approved,
            )
        except (OSError, RuntimeError, ValueError) as error:
            result = None
            cleanup_error: RuntimeError | None = RuntimeError(
                redact_sensitive_text(str(error))
            )
        else:
            cleanup_error = None
        try:
            self.cleanup_bridge.finished.emit(result, cleanup_error)
        except RuntimeError:
            return

    def _cleanup_finished(
        self, result: CleanupResult | None, error: RuntimeError | None
    ) -> None:
        self.maintenance_pending = False
        self.start_button.setEnabled(self.workspace is not None)
        self._refresh_history()
        if result is None:
            message = self._actionable_message(
                str(error or self._t("cleanup.incomplete")),
                self._t("cleanup.retry_action"),
            )
            self.status.setText(message)
            QMessageBox.critical(self, self._t("cleanup.failed_title"), message)
            return
        self.status.setText(self._t("cleanup.done", count=result.removed_files, size=_human_bytes(result.removed_bytes)))

    def _maybe_start_first_run_check(self) -> None:
        if (
            not self.first_run_pending
            or self.first_run_dialog_active
            or self.workspace is None
            or self.maintenance_pending
            or self._process_is_running()
        ):
            return
        self._show_system_status(_first_run=True)

    def _open_system_status(self) -> None:
        if hasattr(self, "tabs"):
            self.tabs.setCurrentWidget(self.system_tab)
        self._show_system_status(_inline=True)

    def _show_system_status(
        self,
        *,
        verify_model_hashes: bool = False,
        _first_run: bool = False,
        _inline: bool = False,
    ) -> None:
        if self.maintenance_pending:
            return
        if self.workspace is None:
            QMessageBox.information(
                self,
                self._t("health.storage_title"),
                self._t("health.storage_body"),
            )
            return
        self.health_dialog_mode = (
            "first_run" if _first_run else "inline" if _inline else "system"
        )
        self.maintenance_pending = True
        self.system_status_button.setEnabled(False)
        self.start_button.setEnabled(False)
        self.status.setText(
            self._t("health.verifying_models")
            if verify_model_hashes
            else self._t("health.first_check")
            if _first_run
            else self._t("health.checking")
        )
        if _inline:
            self.system_summary.setPlainText(self._t("health.checking"))
        workspace = self.workspace
        self.health_thread = threading.Thread(
            target=self._build_system_status_in_background,
            args=(workspace, verify_model_hashes),
            name="KSI Local Studio-Sistem-Durumu",
            daemon=True,
        )
        self.health_thread.start()

    def _build_system_status_in_background(
        self, workspace: WorkspacePaths, verify_model_hashes: bool
    ) -> None:
        try:
            report = build_acceptance_report(
                workspace, verify_model_hashes=verify_model_hashes
            )
        except (OSError, RuntimeError, ValueError) as error:
            report = None
            health_error: RuntimeError | None = RuntimeError(
                redact_sensitive_text(str(error))
            )
        else:
            health_error = None
        try:
            self.health_bridge.finished.emit(report, health_error)
        except RuntimeError:
            return

    def _system_status_finished(
        self, report: AcceptanceReport | None, error: RuntimeError | None
    ) -> None:
        dialog_mode = self.health_dialog_mode
        self.health_dialog_mode = "system"
        self.maintenance_pending = False
        self.system_status_button.setEnabled(True)
        if not self._process_is_running() and not self.preflight_pending and self.workspace:
            self.start_button.setEnabled(True)
        self._refresh_history()
        if report is None:
            message = self._actionable_message(
                str(error or self._t("health.incomplete")),
                self._t("health.retry_action"),
            )
            self.status.setText(message)
            if dialog_mode == "inline":
                self.system_summary.setPlainText(message)
            else:
                QMessageBox.critical(self, self._t("health.failed_title"), message)
            return
        self.status.setText(
            self._t("health.ready_status")
            if report.passed
            else self._t("health.review_status")
        )
        if dialog_mode == "inline":
            self.system_summary.setPlainText(
                format_acceptance_report(report, language=self.preferences.ui_language)
            )
            return
        if dialog_mode == "first_run":
            if not self.first_run_pending:
                return
            self.first_run_dialog_active = True
            try:
                dialog = FirstRunWizard(
                    report,
                    self,
                    language=self.preferences.ui_language,
                    application_location=self.preferences.application_location,
                    workspace_location=self.preferences.workspace_location,
                )
                if dialog.exec() == QDialog.DialogCode.Accepted:
                    self.first_run_pending = False
                    self.preferences = replace(
                        self.preferences,
                        onboarding_version=ONBOARDING_VERSION,
                        application_location=str(dialog.application_location.currentData()),
                        workspace_location=str(dialog.workspace_location.currentData()),
                    )
                    try:
                        save_preferences(self.preferences)
                    except OSError as save_error:
                        self.status.setText(
                            self._actionable_message(
                                str(save_error),
                                self._t("first.preference_save_action"),
                            )
                        )
            finally:
                self.first_run_dialog_active = False
            return
        dialog = SystemStatusDialog(
            report, self, language=self.preferences.ui_language
        )
        dialog.full_verification_requested.connect(
            lambda: self._show_system_status(verify_model_hashes=True)
        )
        dialog.exec()

    def closeEvent(self, event: QCloseEvent) -> None:
        controller = getattr(self, "tool_controller", None)
        if controller is not None and controller.busy:
            controller.cancel()
            event.ignore()
            QTimer.singleShot(200, self.close)
            return
        if self.maintenance_pending:
            QMessageBox.information(
                self,
                self._t("window.busy_title"),
                self._t("window.busy_body"),
            )
            event.ignore()
            return
        self.closing = True
        self.workspace_timer.stop()
        if self._process_is_running():
            assert self.process is not None
            self.process.blockSignals(True)
            self._terminate_process(self.process)
            if not self.process.waitForFinished(1500):
                self.process.kill()
                self.process.waitForFinished(1500)
        event.accept()


def main() -> int:
    lock_path = default_database_path().parent / "app.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_handle.close()
        return 0
    application = QApplication(sys.argv)
    application.setApplicationName("KSI Local Studio")
    window = MainWindow()
    window.show()
    if os.environ.get("KSI_ACCEPTANCE_MODE") == "1":
        try:
            close_after_ms = int(
                os.environ.get("KSI_LIFECYCLE_CHECK_MS", "0") or 0
            )
        except ValueError:
            close_after_ms = 0
        if 50 <= close_after_ms <= 10_000:
            QTimer.singleShot(close_after_ms, window.close)
    exit_code = application.exec()
    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
    lock_handle.close()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
