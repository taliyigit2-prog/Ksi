"""Architecture-aware, network-free access to a verified installation payload.

The manifest is created by the clean package builder, not by a user's existing
runtime. File hashes detect corruption; trusted distribution/signature checks
are a separate release gate. Development may use PATH, bundled apps may not.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import sys
import threading
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable


MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_PAYLOAD_FILES = 50000
ARCHITECTURES = {"arm64", "x86_64"}
_MODEL_INSTALL_CACHE: dict[tuple[str, str], tuple] = {}
_MODEL_INSTALL_LOCK = threading.Lock()


def host_architecture(machine: str | None = None) -> str:
    name = (machine or platform.machine()).casefold()
    mapped = {"aarch64": "arm64", "amd64": "x86_64"}.get(name, name)
    if mapped not in ARCHITECTURES:
        raise RuntimeError("Bu işlemci mimarisi KSI tarafından desteklenmiyor.")
    return mapped


def bundle_root() -> Path | None:
    configured = os.environ.get("KSI_BUNDLE_ROOT")
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute() or path.is_symlink():
            raise ValueError("Paket kaynak yolu mutlak, normal bir klasör olmalıdır.")
        return path.resolve()
    # Packaged Python is inside Contents/Resources/runtime. Never search HOME.
    for parent in Path(sys.executable).absolute().parents:
        if parent.name == "Resources" and parent.parent.name == "Contents":
            return parent.resolve()
    return None


def safe_member(root: Path, value: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError("Geçersiz paket dosyası yolu.")
    parts = PurePosixPath(value)
    if parts.is_absolute() or any(part in {"", ".", ".."} for part in value.split("/")):
        raise ValueError("Paket dosyası yolu kaynak kökünü aşamaz.")
    base = root.resolve()
    candidate = base.joinpath(*parts.parts)
    current = base
    for part in parts.parts:
        current /= part
        if current.is_symlink():
            raise ValueError("Paket verisinde sembolik bağlantıya izin verilmez.")
    if not candidate.resolve().is_relative_to(base):
        raise ValueError("Paket dosyası kaynak kökünün dışında.")
    return candidate


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class PayloadFile:
    path: str
    sha256: str
    size: int
    role: str
    identifier: str


@dataclass(frozen=True)
class OfflinePayload:
    root: Path
    architecture: str
    files: tuple[PayloadFile, ...]

    @classmethod
    def load(cls, root: Path, *, architecture: str | None = None) -> OfflinePayload:
        if root.is_symlink():
            raise ValueError("Paket kökü sembolik bağlantı olamaz.")
        base = root.resolve()
        manifest = safe_member(base, "offline-manifest.json")
        if manifest.stat().st_size > MAX_MANIFEST_BYTES:
            raise ValueError("Paket manifesti boyut sınırını aşıyor.")
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise ValueError("Paket manifesti sürümü desteklenmiyor.")
        target = host_architecture(architecture)
        if data.get("architecture") != target:
            raise RuntimeError("Bu kurulum paketi bu Mac'in mimarisiyle eşleşmiyor.")
        rows = data.get("files")
        if not isinstance(rows, list) or not rows or len(rows) > MAX_PAYLOAD_FILES:
            raise ValueError("Paket dosya listesi geçersiz.")
        result: list[PayloadFile] = []
        seen: set[str] = set()
        identifiers: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Paket dosya kaydı geçersiz.")
            try:
                entry = PayloadFile(**{key: row[key] for key in PayloadFile.__annotations__})
            except (KeyError, TypeError) as error:
                raise ValueError("Paket dosya kaydı eksik.") from error
            safe_member(base, entry.path)
            if entry.path in seen or entry.path.casefold() in seen:
                raise ValueError("Paket dosya listesinde çakışma var.")
            seen.update((entry.path, entry.path.casefold()))
            if not isinstance(entry.sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", entry.sha256):
                raise ValueError("Paket dosyası SHA-256 değeri geçersiz.")
            if type(entry.size) is not int or not 0 <= entry.size <= 64 * 1024**3:
                raise ValueError("Paket dosyası boyutu geçersiz.")
            if entry.role not in {"model", "tool", "license", "support"}:
                raise ValueError("Paket dosyası rolü geçersiz.")
            if not isinstance(entry.identifier, str) or not re.fullmatch(
                r"[A-Za-z0-9_.-]{1,128}", entry.identifier
            ):
                raise ValueError("Paket bileşeni kimliği geçersiz.")
            if entry.role in {"model", "tool"}:
                key = f"{entry.role}:{entry.identifier}"
                if key in identifiers:
                    raise ValueError("Paket bileşeni kimliği çakışıyor.")
                identifiers.add(key)
            result.append(entry)
        return cls(base, target, tuple(result))

    def verify(self, entry: PayloadFile) -> Path:
        path = safe_member(self.root, entry.path)
        if not path.is_file() or path.stat().st_size != entry.size:
            raise RuntimeError("Kurulum paketindeki bir dosya eksik veya bozuk.")
        if digest_file(path) != entry.sha256:
            raise RuntimeError("Kurulum dosyası bütünlük doğrulamasından geçmedi.")
        return path

    def component(self, role: str, identifier: str) -> Path:
        for entry in self.files:
            if entry.role == role and entry.identifier == identifier:
                return self.verify(entry)
        raise RuntimeError(f"Çevrimdışı paket bileşeni bulunamadı: {identifier}")

    def install_models(
        self, destination: Path, *, on_progress: Callable[[int, int], None] | None = None
    ) -> None:
        # Removable-storage polling must not hash gigabytes every five seconds.
        # The first use still verifies hashes; changes to the manifest or any
        # installed file's size/mtime invalidate the in-process shortcut.
        with _MODEL_INSTALL_LOCK:
            if destination.is_symlink():
                raise ValueError("Model hedefi sembolik bağlantı olamaz.")
            entries = [entry for entry in self.files if entry.role == "model"]
            if any(not entry.path.startswith("models/") for entry in entries):
                raise ValueError("Model kaydı models dizini içinde olmalıdır.")
            key = (str(self.root), str(destination.absolute()))

            def stamp():
                values = []
                for entry in entries:
                    path = safe_member(destination, entry.path.removeprefix("models/"))
                    try:
                        stat = path.stat()
                    except FileNotFoundError:
                        return None
                    values.append((entry.path, entry.sha256, entry.size, stat.st_size, stat.st_mtime_ns))
                return tuple(values)

            current = stamp()
            if key in _MODEL_INSTALL_CACHE and current == _MODEL_INSTALL_CACHE[key]:
                return
            self._install_models(destination, on_progress=on_progress)
            verified = stamp()
            if verified is not None:
                if len(_MODEL_INSTALL_CACHE) >= 32:
                    _MODEL_INSTALL_CACHE.pop(next(iter(_MODEL_INSTALL_CACHE)))
                _MODEL_INSTALL_CACHE[key] = verified

    def _install_models(
        self, destination: Path, *, on_progress: Callable[[int, int], None] | None = None
    ) -> None:
        """Copy verified bundled models atomically, preserving existing files.

        No network or deletion. Interrupted copies may be retried. A conflicting
        user file is never replaced merely because a manifest names that path.
        """
        if destination.is_symlink():
            raise ValueError("Model hedefi sembolik bağlantı olamaz.")
        destination.mkdir(parents=True, exist_ok=True, mode=0o700)
        entries = [entry for entry in self.files if entry.role == "model"]
        pending: list[tuple[PayloadFile, Path]] = []
        for entry in entries:
            if not entry.path.startswith("models/"):
                raise ValueError("Model kaydı models dizini içinde olmalıdır.")
            target = safe_member(destination, entry.path.removeprefix("models/"))
            if target.exists():
                if not target.is_file() or digest_file(target) != entry.sha256:
                    raise FileExistsError("Mevcut model farklı; kullanıcı dosyasına yazılmadı.")
            else:
                pending.append((entry, target))
        required = sum(entry.size for entry, _ in pending)
        if shutil.disk_usage(destination).free < required + 256 * 1024**2:
            raise OSError("Çevrimdışı modellerin kurulumu için yeterli boş alan yok.")
        completed = 0
        for entry, target in pending:
            source = self.verify(entry)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = target.with_name(f".{target.name}.install-{os.getpid()}.part")
            created = False
            try:
                # Exclusive creation prevents following a planted symlink.
                with temporary.open("xb") as output, source.open("rb") as input_stream:
                    created = True
                    for block in iter(lambda: input_stream.read(1024 * 1024), b""):
                        output.write(block)
                        completed += len(block)
                        if on_progress:
                            on_progress(completed, required)
                    output.flush()
                    os.fsync(output.fileno())
                if digest_file(temporary) != entry.sha256:
                    raise OSError("Kopyalanan model bütünlük denetiminden geçmedi.")
                # A competing installer cannot cause an overwrite.
                os.link(temporary, target)
                target.chmod(0o600)
            finally:
                if created:
                    temporary.unlink(missing_ok=True)


def tool_path(name: str, *, required: bool = True) -> str | None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
        raise ValueError("Geçersiz araç adı.")
    root = bundle_root()
    if root is not None:
        candidate = OfflinePayload.load(root).component("tool", name)
        if not os.access(candidate, os.X_OK):
            raise RuntimeError("Paket aracı çalıştırılabilir değil.")
        return str(candidate)
    selected = shutil.which(name)
    if selected is None and required:
        raise RuntimeError(f"Geliştirme aracı bulunamadı: {name}")
    return selected
