"""Bounded inspection and atomic import for untrusted local documents."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import time
import unicodedata
import uuid
import zipfile
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Callable
from xml.etree import ElementTree

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from ksi_local.exporter import _rename_exclusive
from ksi_local.language_detection import detect_document_language
from ksi_local.network_policy import local_worker_environment


class DocumentFormat(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    MARKDOWN = "md"
    TEXT = "txt"


class DocumentImportCancelled(RuntimeError):
    """Raised before an inspected document becomes visible on the disk."""


DOCUMENT_SUFFIXES = frozenset({".pdf", ".docx", ".md", ".txt"})
DOCUMENT_SIZE_LIMITS = {
    DocumentFormat.PDF: 200 * 1024**2,
    DocumentFormat.DOCX: 100 * 1024**2,
    DocumentFormat.MARKDOWN: 20 * 1024**2,
    DocumentFormat.TEXT: 20 * 1024**2,
}
MAX_PDF_PAGES = 2_000
MAX_PDF_RAW_CONTENT_BYTES = 128 * 1024**2
MAX_ZIP_ENTRIES = 10_000
MAX_ZIP_MEMBER_BYTES = 128 * 1024**2
MAX_ZIP_UNCOMPRESSED_BYTES = 512 * 1024**2
MAX_ZIP_RATIO = 200
STALE_IMPORT_SECONDS = 6 * 60 * 60


_FORMAT_LABELS = {
    DocumentFormat.PDF: "PDF",
    DocumentFormat.DOCX: "Word belgesi (DOCX)",
    DocumentFormat.MARKDOWN: "Markdown",
    DocumentFormat.TEXT: "Düz metin (TXT)",
}
_PDF_BLOCKING_MARKERS = {
    b"/JavaScript": "PDF içinde JavaScript eylemi bulundu.",
    b"/JS": "PDF içinde JavaScript kısayol eylemi bulundu.",
    b"/OpenAction": "PDF açılış eylemi içeriyor.",
    b"/AA": "PDF otomatik ek eylem içeriyor.",
    b"/Launch": "PDF harici uygulama başlatma eylemi içeriyor.",
    b"/EmbeddedFile": "PDF gömülü dosya içeriyor.",
    b"/RichMedia": "PDF etkin zengin medya içeriyor.",
    b"/SubmitForm": "PDF form verisi gönderme eylemi içeriyor.",
    b"/GoToR": "PDF uzak belge eylemi içeriyor.",
}


@dataclass(frozen=True)
class DocumentInspection:
    format: DocumentFormat
    format_label: str
    filename: str
    size_bytes: int
    sha256: str
    page_count: int | None
    block_count: int | None
    detected_language: str | None
    language_confidence: float | None
    ocr_likely_pages: int | None
    text_preview_available: bool
    accepted: bool
    warnings: tuple[str, ...]
    blocking_reasons: tuple[str, ...]
    package_entries: int | None = None
    package_uncompressed_bytes: int | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "DocumentInspection":
        return cls(
            format=DocumentFormat(str(payload["format"])),
            format_label=str(payload["format_label"]),
            filename=str(payload["filename"]),
            size_bytes=int(payload["size_bytes"]),
            sha256=str(payload["sha256"]),
            page_count=(
                int(payload["page_count"])
                if payload.get("page_count") is not None
                else None
            ),
            block_count=(
                int(payload["block_count"])
                if payload.get("block_count") is not None
                else None
            ),
            detected_language=(
                str(payload["detected_language"])
                if payload.get("detected_language")
                else None
            ),
            language_confidence=(
                float(payload["language_confidence"])
                if payload.get("language_confidence") is not None
                else None
            ),
            ocr_likely_pages=(
                int(payload["ocr_likely_pages"])
                if payload.get("ocr_likely_pages") is not None
                else None
            ),
            text_preview_available=bool(payload["text_preview_available"]),
            accepted=bool(payload["accepted"]),
            warnings=tuple(str(item) for item in payload.get("warnings", [])),
            blocking_reasons=tuple(
                str(item) for item in payload.get("blocking_reasons", [])
            ),
            package_entries=(
                int(payload["package_entries"])
                if payload.get("package_entries") is not None
                else None
            ),
            package_uncompressed_bytes=(
                int(payload["package_uncompressed_bytes"])
                if payload.get("package_uncompressed_bytes") is not None
                else None
            ),
        )


@dataclass(frozen=True)
class ImportedDocument:
    path: Path
    size_bytes: int
    sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "verification": "size+sha256",
        }


def _format_for(path: Path) -> DocumentFormat:
    suffix = path.suffix.casefold()
    try:
        return DocumentFormat(suffix.removeprefix("."))
    except ValueError as error:
        raise ValueError("Yalnız PDF, DOCX, MD veya TXT belgesi seçilebilir.") from error


def _validate_filename(path: Path) -> None:
    name = path.name
    if not name or len(name.encode("utf-8")) > 240:
        raise ValueError("Belge dosya adı boş veya çok uzun.")
    if any(unicodedata.category(character) == "Cc" for character in name):
        raise ValueError("Belge dosya adı kontrol karakteri içeriyor.")
    if any(character in '<>:"/\\|?*' for character in name):
        raise ValueError("Belge dosya adı güvenli olmayan karakter içeriyor.")


def _open_stable_source(path: str | Path) -> tuple[Path, BinaryIO, os.stat_result]:
    unresolved = Path(path).expanduser()
    if unresolved.is_symlink():
        raise ValueError("Sembolik bağlantı belge olarak içe aktarılamaz.")
    _validate_filename(unresolved)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(unresolved, flags)
    except OSError as error:
        raise OSError("Belge güvenli biçimde açılamadı.") from error
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode):
        os.close(descriptor)
        raise ValueError("Seçilen belge normal bir dosya değil.")
    try:
        resolved = unresolved.resolve(strict=True)
    except OSError:
        os.close(descriptor)
        raise
    return resolved, os.fdopen(descriptor, "rb"), info


def _sha256_stream(handle: BinaryIO) -> str:
    handle.seek(0)
    digest = hashlib.sha256()
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
    handle.seek(0)
    return digest.hexdigest()


def _detect_language(sample: str, warnings: list[str]) -> tuple[str | None, float | None]:
    try:
        result = detect_document_language(sample)
    except RuntimeError:
        warnings.append(
            "Dil ön incelemede güvenle belirlenemedi; sonraki aşamada yeniden ölçülecek."
        )
        return None, None
    return result.code, result.confidence


def _scan_stream_markers(
    handle: BinaryIO, markers: set[bytes]
) -> set[bytes]:
    found: set[bytes] = set()
    maximum = max(map(len, markers), default=1)
    overlap = b""
    handle.seek(0)
    while chunk := handle.read(1024 * 1024):
        window = overlap + chunk
        for marker in markers - found:
            if marker in window:
                found.add(marker)
        overlap = window[-maximum:]
    handle.seek(0)
    return found


def _resolve_pdf_object(value: object) -> object:
    getter = getattr(value, "get_object", None)
    return getter() if callable(getter) else value


def _pdf_object_markers(reader: PdfReader) -> set[bytes]:
    """Find active-content names in parsed PDF objects, never in stream bytes."""
    wanted = set(_PDF_BLOCKING_MARKERS) | {b"/URI"}
    found: set[bytes] = set()
    pending: list[object] = [reader.trailer]
    seen_indirect: set[tuple[int, int]] = set()
    seen_direct: set[int] = set()
    visited = 0
    while pending:
        value = pending.pop()
        indirect_id = getattr(value, "idnum", None)
        if indirect_id is not None:
            identity = (int(indirect_id), int(getattr(value, "generation", 0)))
            if identity in seen_indirect:
                continue
            seen_indirect.add(identity)
        try:
            resolved = _resolve_pdf_object(value)
        except (PdfReadError, RecursionError, ValueError):
            continue
        if indirect_id is None and isinstance(resolved, (dict, list, tuple)):
            identity_direct = id(resolved)
            if identity_direct in seen_direct:
                continue
            seen_direct.add(identity_direct)
        visited += 1
        if visited > 100_000:
            raise ValueError("PDF nesne ağacı güvenli inceleme sınırını aşıyor.")
        if isinstance(resolved, dict):
            for key, child in resolved.items():
                key_bytes = str(key).encode("ascii", "ignore")
                if key_bytes in wanted:
                    found.add(key_bytes)
                if isinstance(child, (str, bytes)):
                    child_name = (
                        child.encode("ascii", "ignore")
                        if isinstance(child, str)
                        else child
                    )
                    if child_name in wanted:
                        found.add(child_name)
                pending.append(child)
        elif isinstance(resolved, (list, tuple)):
            pending.extend(resolved)
    return found


def _pdf_inspection(
    handle: BinaryIO,
    *,
    filename: str,
    size_bytes: int,
    digest: str,
) -> DocumentInspection:
    handle.seek(0)
    if handle.read(5) != b"%PDF-":
        raise ValueError("Dosya uzantısı PDF fakat PDF imzası bulunamadı.")
    handle.seek(max(0, size_bytes - 4096))
    if b"%%EOF" not in handle.read():
        raise ValueError("PDF tamamlanmamış veya sonlandırma işareti bozuk.")
    handle.seek(0)
    try:
        reader = PdfReader(handle, strict=True)
        encrypted = bool(reader.is_encrypted)
    except (PdfReadError, RecursionError, ValueError) as error:
        raise ValueError("PDF yapısı veya xref tablosu güvenle okunamadı.") from error
    blockers: list[str] = []
    warnings: list[str] = []
    if encrypted:
        blockers.append("Şifreli PDF bu aşamada işlenemez.")
        page_count = None
        ocr_pages = None
    else:
        try:
            page_count = len(reader.pages)
        except (PdfReadError, RecursionError, ValueError) as error:
            raise ValueError("PDF sayfa ağacı güvenle okunamadı.") from error
        if page_count < 1:
            blockers.append("PDF içinde sayfa bulunamadı.")
        elif page_count > MAX_PDF_PAGES:
            blockers.append(f"PDF {MAX_PDF_PAGES} sayfalık güvenlik sınırını aşıyor.")
        raw_content_bytes = 0
        ocr_pages = 0
        if page_count <= MAX_PDF_PAGES:
            for page in reader.pages:
                contents = page.raw_get("/Contents") if "/Contents" in page else None
                contents = _resolve_pdf_object(contents)
                streams = contents if isinstance(contents, list) else [contents]
                page_has_content = False
                for stream in streams:
                    resolved = _resolve_pdf_object(stream)
                    raw = getattr(resolved, "_data", None)
                    if isinstance(raw, bytes):
                        raw_content_bytes += len(raw)
                        page_has_content = page_has_content or bool(raw)
                resources = _resolve_pdf_object(page.get("/Resources"))
                has_fonts = bool(
                    isinstance(resources, dict) and resources.get("/Font")
                )
                has_images = bool(
                    isinstance(resources, dict) and resources.get("/XObject")
                )
                if has_images and not has_fonts and page_has_content:
                    ocr_pages += 1
        if raw_content_bytes > MAX_PDF_RAW_CONTENT_BYTES:
            blockers.append("PDF içerik akışları güvenli ham boyut sınırını aşıyor.")
        if page_count and ocr_pages == page_count:
            warnings.append("Bütün PDF sayfaları taranmış görünüyor; OCR gerekecek.")
        elif ocr_pages:
            warnings.append(f"Yaklaşık {ocr_pages} PDF sayfasında OCR gerekebilir.")

    found = set() if encrypted else _pdf_object_markers(reader)
    blockers.extend(_PDF_BLOCKING_MARKERS[item] for item in _PDF_BLOCKING_MARKERS if item in found)
    if b"/URI" in found:
        warnings.append("PDF harici bağlantı içeriyor; KSI Local Studio bu bağlantıları açmayacak.")
    blockers = list(dict.fromkeys(blockers))
    return DocumentInspection(
        format=DocumentFormat.PDF,
        format_label=_FORMAT_LABELS[DocumentFormat.PDF],
        filename=filename,
        size_bytes=size_bytes,
        sha256=digest,
        page_count=page_count,
        block_count=None,
        detected_language=None,
        language_confidence=None,
        ocr_likely_pages=ocr_pages,
        text_preview_available=False,
        accepted=not blockers,
        warnings=tuple(warnings),
        blocking_reasons=tuple(blockers),
    )


def _safe_zip_member(name: str) -> bool:
    if not name or "\\" in name or name.startswith(("/", "~")) or ":" in name:
        return False
    path = PurePosixPath(name)
    return not path.is_absolute() and ".." not in path.parts and len(name) <= 240


def _xml_root(content: bytes, label: str) -> ElementTree.Element:
    if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
        raise ValueError(f"{label} güvenli olmayan XML varlık tanımı içeriyor.")
    try:
        return ElementTree.fromstring(content)
    except ElementTree.ParseError as error:
        raise ValueError(f"{label} XML yapısı bozuk.") from error


def _docx_inspection(
    handle: BinaryIO,
    *,
    filename: str,
    size_bytes: int,
    digest: str,
) -> DocumentInspection:
    handle.seek(0)
    if handle.read(4) not in {b"PK\x03\x04", b"PK\x05\x06"}:
        raise ValueError("Dosya uzantısı DOCX fakat ZIP/OOXML imzası bulunamadı.")
    handle.seek(0)
    try:
        package = zipfile.ZipFile(handle)
        entries = package.infolist()
    except (OSError, zipfile.BadZipFile) as error:
        raise ValueError("DOCX paketi bozuk veya okunamıyor.") from error
    if len(entries) > MAX_ZIP_ENTRIES:
        raise ValueError("DOCX paketinde güvenli sınırdan fazla ZIP girdisi var.")
    names: set[str] = set()
    total_uncompressed = 0
    for item in entries:
        if not _safe_zip_member(item.filename):
            raise ValueError("DOCX paketinde yol geçişi veya güvensiz üye adı bulundu.")
        normalized = item.filename.casefold()
        if normalized in names:
            raise ValueError("DOCX paketinde yinelenen ZIP üyesi bulundu.")
        names.add(normalized)
        if item.flag_bits & 0x1:
            raise ValueError("Şifreli DOCX ZIP üyesi desteklenmiyor.")
        if item.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
            raise ValueError("DOCX paketinde desteklenmeyen sıkıştırma yöntemi var.")
        if item.file_size > MAX_ZIP_MEMBER_BYTES:
            raise ValueError("DOCX paketindeki tek bir üye güvenli boyut sınırını aşıyor.")
        total_uncompressed += item.file_size
        if total_uncompressed > MAX_ZIP_UNCOMPRESSED_BYTES:
            raise ValueError("DOCX açılmış toplam boyutu güvenli sınırı aşıyor.")
        if item.file_size and item.file_size / max(item.compress_size, 1) > MAX_ZIP_RATIO:
            raise ValueError("DOCX paketinde aşırı sıkıştırma oranı bulundu.")
    required = {"[content_types].xml", "_rels/.rels", "word/document.xml"}
    if not required.issubset(names):
        raise ValueError("DOCX temel OOXML parçalarından biri eksik.")
    try:
        corrupt = package.testzip()
    except (OSError, RuntimeError, zipfile.BadZipFile) as error:
        raise ValueError("DOCX ZIP bütünlük denetimi başarısız.") from error
    if corrupt:
        raise ValueError(f"DOCX içinde bozuk ZIP üyesi var: {Path(corrupt).name}")

    blockers: list[str] = []
    warnings: list[str] = []
    macro_names = [
        item.filename
        for item in entries
        if item.filename.casefold().endswith("vbaproject.bin")
        or "macroenabled" in item.filename.casefold()
    ]
    content_types = package.read("[Content_Types].xml")
    if b"macroenabled" in content_types.lower() or macro_names:
        blockers.append("DOCX makro/VBA içeriği taşıyor.")
    if any(item.filename.casefold().startswith("word/embeddings/") for item in entries):
        blockers.append("DOCX gömülü nesne veya dosya içeriyor.")
    relationship_files = [
        item for item in entries if item.filename.casefold().endswith(".rels")
    ]
    for relationship in relationship_files:
        content = package.read(relationship)
        if re.search(rb"TargetMode\s*=\s*['\"]External['\"]", content, re.IGNORECASE):
            blockers.append("DOCX harici bağlantı/ilişki içeriyor.")
            break

    document_xml = package.read("word/document.xml")
    root = _xml_root(document_xml, "DOCX ana belge")
    text_nodes = [
        element.text or ""
        for element in root.iter()
        if element.tag.rsplit("}", 1)[-1] == "t" and element.text
    ]
    paragraph_count = sum(
        1 for element in root.iter() if element.tag.rsplit("}", 1)[-1] == "p"
    )
    table_count = sum(
        1 for element in root.iter() if element.tag.rsplit("}", 1)[-1] == "tbl"
    )
    sample = " ".join(text_nodes)[:50_000]
    language, confidence = _detect_language(sample, warnings)
    page_count: int | None = None
    if "docprops/app.xml" in names:
        app_root = _xml_root(package.read("docProps/app.xml"), "DOCX özellikleri")
        pages = next(
            (
                element.text
                for element in app_root.iter()
                if element.tag.rsplit("}", 1)[-1] == "Pages"
            ),
            None,
        )
        if pages and pages.isdigit() and int(pages) > 0:
            page_count = int(pages)
    if page_count is None:
        warnings.append("DOCX sayfa sayısı belge özelliklerinde yok; blok sayısı gösteriliyor.")
    blockers = list(dict.fromkeys(blockers))
    return DocumentInspection(
        format=DocumentFormat.DOCX,
        format_label=_FORMAT_LABELS[DocumentFormat.DOCX],
        filename=filename,
        size_bytes=size_bytes,
        sha256=digest,
        page_count=page_count,
        block_count=paragraph_count + table_count,
        detected_language=language,
        language_confidence=confidence,
        ocr_likely_pages=0,
        text_preview_available=bool(sample.strip()),
        accepted=not blockers,
        warnings=tuple(warnings),
        blocking_reasons=tuple(blockers),
        package_entries=len(entries),
        package_uncompressed_bytes=total_uncompressed,
    )


def _text_inspection(
    handle: BinaryIO,
    *,
    format: DocumentFormat,
    filename: str,
    size_bytes: int,
    digest: str,
) -> DocumentInspection:
    handle.seek(0)
    content = handle.read()
    if b"\x00" in content:
        raise ValueError("Metin belgesi ikili içerik veya NUL baytı taşıyor.")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("Metin belgesi UTF-8 olarak güvenle okunamadı.") from error
    if not text.strip():
        raise ValueError("Metin belgesi boş.")
    warnings: list[str] = []
    language, confidence = _detect_language(text[:50_000], warnings)
    if format is DocumentFormat.MARKDOWN:
        blocks = len(
            re.findall(
                r"(?m)^(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|```|[^\s].*)",
                text,
            )
        )
    else:
        blocks = len([item for item in re.split(r"\n\s*\n|\n", text) if item.strip()])
    return DocumentInspection(
        format=format,
        format_label=_FORMAT_LABELS[format],
        filename=filename,
        size_bytes=size_bytes,
        sha256=digest,
        page_count=None,
        block_count=max(blocks, 1),
        detected_language=language,
        language_confidence=confidence,
        ocr_likely_pages=0,
        text_preview_available=True,
        accepted=True,
        warnings=tuple(warnings),
        blocking_reasons=(),
    )


def inspect_document(path: str | Path) -> DocumentInspection:
    """Inspect an untrusted document with strict size, signature and structure bounds."""
    resolved, handle, initial = _open_stable_source(path)
    with handle:
        format = _format_for(resolved)
        limit = DOCUMENT_SIZE_LIMITS[format]
        if initial.st_size <= 0:
            raise ValueError("Belge dosyası boş.")
        if initial.st_size > limit:
            raise ValueError(
                f"{_FORMAT_LABELS[format]} en fazla {limit // 1024**2} MiB olabilir."
            )
        digest = _sha256_stream(handle)
        if format is DocumentFormat.PDF:
            result = _pdf_inspection(
                handle,
                filename=resolved.name,
                size_bytes=initial.st_size,
                digest=digest,
            )
        elif format is DocumentFormat.DOCX:
            result = _docx_inspection(
                handle,
                filename=resolved.name,
                size_bytes=initial.st_size,
                digest=digest,
            )
        else:
            result = _text_inspection(
                handle,
                format=format,
                filename=resolved.name,
                size_bytes=initial.st_size,
                digest=digest,
            )
        final = os.fstat(handle.fileno())
        if (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns) != (
            initial.st_dev,
            initial.st_ino,
            initial.st_size,
            initial.st_mtime_ns,
        ):
            raise OSError("Belge ön inceleme sırasında değişti; yeniden seçin.")
        return result


def inspect_document_isolated(
    path: str | Path, *, timeout_seconds: int = 30
) -> DocumentInspection:
    """Inspect in a local-only worker so malformed parsers cannot freeze the GUI."""
    if timeout_seconds < 1 or timeout_seconds > 120:
        raise ValueError("Belge ön inceleme zaman aşımı 1–120 saniye arasında olmalıdır.")
    environment = local_worker_environment()
    environment["PYTHONNOUSERSITE"] = "1"
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "ksi_local.document_worker", "inspect", str(path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=environment,
            shell=False,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("Belge güvenlik incelemesi zaman sınırını aştı.") from error
    if completed.returncode != 0:
        try:
            payload = json.loads(completed.stdout)
            message = str(payload.get("error") or "Belge güvenlik incelemesi başarısız.")
        except json.JSONDecodeError:
            message = "Belge güvenlik incelemesi güvenli biçimde tamamlanamadı."
        raise ValueError(message)
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("Belge işçisi geçersiz sonuç döndürdü.") from error
    if not isinstance(payload, dict):
        raise RuntimeError("Belge işçisi beklenen sonucu döndürmedi.")
    return DocumentInspection.from_dict(payload)


def cleanup_stale_document_imports(
    directory: str | Path, *, older_than_seconds: int = STALE_IMPORT_SECONDS
) -> int:
    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        return 0
    now = time.time()
    removed = 0
    for item in root.glob(".document-import-*.part"):
        try:
            if (
                re.fullmatch(
                    r"\.document-import-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\.part",
                    item.name,
                )
                and item.is_file()
                and not item.is_symlink()
                and now - item.stat().st_mtime >= older_than_seconds
            ):
                item.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def import_document_source(
    source: str | Path,
    destination_directory: str | Path,
    *,
    expected_size: int,
    expected_sha256: str,
    cancel_check: Callable[[], bool] | None = None,
) -> ImportedDocument:
    """Copy a stable source to a hidden disk file, verify, then publish exclusively."""
    source_path, handle, initial = _open_stable_source(source)
    destination = Path(destination_directory).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    cleanup_stale_document_imports(destination)
    format = _format_for(source_path)
    target = destination / f"original.{format.value}"
    if target.exists():
        handle.close()
        raise FileExistsError("Bu iş klasöründe içe aktarılmış belge zaten var.")
    if initial.st_size != expected_size:
        handle.close()
        raise OSError("Belge ön incelemeden sonra değişti; yeniden inceleyin.")
    temporary = destination / f".document-import-{uuid.uuid4()}.part"
    digest = hashlib.sha256()
    copied = 0
    published = False
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        with handle, os.fdopen(descriptor, "wb") as output:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                if cancel_check is not None and cancel_check():
                    raise DocumentImportCancelled(
                        "Belge içe aktarma kullanıcı tarafından iptal edildi."
                    )
                copied += len(chunk)
                if copied > DOCUMENT_SIZE_LIMITS[format]:
                    raise OSError("Belge kopyalama sırasında güvenli boyut sınırını aştı.")
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
            final = os.fstat(handle.fileno())
        actual_digest = digest.hexdigest()
        if (copied, actual_digest) != (expected_size, expected_sha256.casefold()):
            raise OSError("Belge kopyası boyut veya SHA-256 doğrulamasından geçmedi.")
        if (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns) != (
            initial.st_dev,
            initial.st_ino,
            initial.st_size,
            initial.st_mtime_ns,
        ):
            raise OSError("Kaynak belge kopyalama sırasında değişti.")
        if cancel_check is not None and cancel_check():
            raise DocumentImportCancelled("Belge içe aktarma kullanıcı tarafından iptal edildi.")
        _rename_exclusive(temporary, target)
        published = True
        if target.stat().st_size != expected_size:
            raise OSError("diske alınan belge boyutu doğrulanamadı.")
        return ImportedDocument(target, copied, actual_digest)
    except BaseException:
        temporary.unlink(missing_ok=True)
        if published:
            target.unlink(missing_ok=True)
        raise
    finally:
        if not handle.closed:
            handle.close()
