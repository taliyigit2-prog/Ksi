"""Pinned build-only downloads; never used by the offline installed app."""

import hashlib
import os
import re
import shutil
import tarfile
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from ksi_local.bundle_runtime import digest_file


class SecureRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        if urlsplit(new_url).scheme != "https":
            raise ValueError("Sabitlenmiş yapı indirmesi HTTPS dışına yönlendirilemez.")
        return super().redirect_request(request, response, code, message, headers, new_url)


def fetch_pinned_input(specification: dict, destination: Path, *, on_progress=None) -> Path:
    if not isinstance(specification, dict):
        raise ValueError("Yapı girdisi tanımı geçersiz.")
    url, expected, size = specification.get("url"), specification.get("sha256"), specification.get("size")
    if not isinstance(url, str) or not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected) or type(size) is not int or not 0 < size <= 64 * 1024**3:
        raise ValueError("Yapı girdisi açık URL, SHA-256 ve boyut gerektirir.")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Yapı girdisi temiz ve herkese açık HTTPS URL'si olmalıdır.")
    if not destination.is_absolute() or destination.is_symlink():
        raise ValueError("Yapı önbellek hedefi mutlak ve normal dosya olmalıdır.")
    if destination.exists():
        if destination.is_file() and destination.stat().st_size == size and digest_file(destination) == expected:
            return destination
        raise FileExistsError("Mevcut yapı önbelleği farklı; üzerine yazılmadı.")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if shutil.disk_usage(destination.parent).free < size + 512 * 1024**2:
        raise OSError("Sabitlenmiş yapı girdisi için yeterli boş alan yok.")
    temporary = destination.with_name("." + destination.name + "." + uuid.uuid4().hex + ".part")
    created = False
    try:
        # No account tokens, cookies or proxy credentials are sent to public sources.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), SecureRedirect())
        request = urllib.request.Request(url, headers={"User-Agent": "KSI-clean-build/1"})
        checksum, received = hashlib.sha256(), 0
        with temporary.open("xb") as output:
            created = True
            with opener.open(request, timeout=45) as response:
                if urlsplit(response.url).scheme != "https":
                    raise ValueError("Yapı indirme yanıtı HTTPS değil.")
                while block := response.read(1024 * 1024):
                    received += len(block)
                    if received > size:
                        raise ValueError("Yapı girdisi sabitlenmiş boyutu aşıyor.")
                    checksum.update(block)
                    output.write(block)
                    if on_progress:
                        on_progress(received, size)
            output.flush()
            os.fsync(output.fileno())
        if received != size or checksum.hexdigest() != expected:
            raise RuntimeError("Yapı girdisi resmî sabitlenmiş digest ile eşleşmiyor.")
        os.link(temporary, destination)
        destination.chmod(0o400)
        return destination
    finally:
        if created:
            temporary.unlink(missing_ok=True)


def extract_python_input(archive: Path, destination: Path, *, sha256: str) -> Path:
    if archive.is_symlink() or not archive.is_file() or digest_file(archive) != sha256:
        raise ValueError("Python arşivi sabitlenmiş digest ile eşleşmiyor.")
    if destination.is_symlink() or destination.exists():
        raise FileExistsError("Temiz Python hedefi yeni olmalıdır.")
    with tarfile.open(archive, "r:gz") as stream:
        members = stream.getmembers()
        if len(members) > 50000 or sum(member.size for member in members) > 1024**3:
            raise ValueError("Python arşivi güvenli genişleme sınırını aşıyor.")
        seen = set()
        for member in members:
            name = member.name.rstrip("/")
            parts = name.split("/")
            if not parts or parts[0] != "python" or any(part in {"", ".", ".."} for part in parts) or "\\" in name or "\x00" in name or name.casefold() in seen:
                raise ValueError("Python arşiv üyesi hedef kökünü aşabilir veya çakışıyor.")
            if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                raise ValueError("Python arşivinde özel cihaz/FIFO dosyası olamaz.")
            seen.add(name.casefold())
        destination.mkdir(parents=True, mode=0o700)
        stream.extractall(destination, members=members, filter="data")
    binary = destination / "python/bin/python3.12"
    if binary.is_symlink() or not binary.is_file() or not os.access(binary, os.X_OK):
        raise ValueError("Temiz Python arşivinde gerçek çalıştırılabilir dosya yok.")
    return binary
