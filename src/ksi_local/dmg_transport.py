"""Native offline DMG transport with an explicit GitHub per-asset bound."""

from __future__ import annotations

import json
import plistlib
import subprocess
import tempfile
from pathlib import Path

from ksi_local.atomic_files import atomic_write_json, atomic_write_text
from ksi_local.bundle_runtime import ARCHITECTURES, OfflinePayload, digest_file


GITHUB_ASSET_LIMIT = 2 * 1024**3


def _command(argv):
    subprocess.run(argv, check=True, stdin=subprocess.DEVNULL, timeout=10800)


def build_dmg_transport(app: Path, destination: Path) -> dict:
    """Package an already-sealed clean app; installation tests remain required."""
    if app.is_symlink() or app.name != "KSI Local Studio.app" or not app.is_dir():
        raise ValueError("Paket girdisi temiz KSI Local Studio.app olmalıdır.")
    resources = app / "Contents/Resources"
    info = app / "Contents/Info.plist"
    if info.is_symlink() or info.stat().st_size > 65536:
        raise ValueError("Uygulama plist yapısı geçersiz.")
    metadata = plistlib.loads(info.read_bytes())
    architecture = metadata.get("KSIArchitecture")
    if architecture not in ARCHITECTURES:
        raise ValueError("Uygulama mimarisi açıkça tanımlı olmalıdır.")
    payload = OfflinePayload.load(resources, architecture=architecture)
    for entry in payload.files:
        payload.verify(entry)
    if not any(entry.role == "model" for entry in payload.files):
        raise ValueError("Çevrimdışı model içermeyen paket dağıtılamaz.")
    if destination.is_symlink() or (destination.exists() and any(destination.iterdir())):
        raise FileExistsError("Dağıtım hedefi yeni veya boş klasör olmalıdır.")
    version = metadata.get("CFBundleShortVersionString")
    if not isinstance(version, str) or not version or any(char not in "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ.-" for char in version):
        raise ValueError("Uygulama sürümü dosya adı için geçersiz.")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    _command(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)])
    signature = subprocess.run(["/usr/bin/codesign", "-dv", str(app)], capture_output=True,
                               text=True, check=True, timeout=30)
    if "Signature=adhoc" not in signature.stderr:
        raise ValueError("Bu dağıtım akışı açıkça ad-hoc imzalı uygulama gerektirir.")
    name = f"KSI-Local-Studio-{version}-{architecture}"
    with tempfile.TemporaryDirectory(prefix="ksi-offline-dmg-") as temporary:
        stage = Path(temporary)
        media = stage / "media"
        media.mkdir()
        # macOS -c creates independent copy-on-write files on APFS. Both roots
        # belong to this build; source files are never hard-linked or changed.
        clone = subprocess.run(["/bin/cp", "-cR", str(app), str(media / app.name)],
                               stdin=subprocess.DEVNULL, capture_output=True, timeout=10800)
        if clone.returncode != 0:
            _command(["/usr/bin/ditto", str(app), str(media / app.name)])
        (media / "Applications").symlink_to("/Applications")
        atomic_write_text(media / "Kurulum.txt", "KSI Local Studio — çevrimdışı kurulum\n\n"
            "Uygulamayı Applications klasörüne sürükleyin ve oradan açın.\n"
            "Modeller paket içindedir; ilk kurulumda internet gerekmez.\n"
            "Apple tarafından noterlenmemiş ad-hoc imzalı pakettir.\n"
            "macOS ilk açılış uyarısında Sistem Ayarları > Gizlilik ve Güvenlik yolunu kullanabilirsiniz.\n")
        image = destination / (name + ".dmg")
        command = ["/usr/bin/hdiutil", "create", "-quiet", "-volname", "KSI Local Studio",
                   "-srcfolder", str(media), "-format", "UDZO", "-imagekey", "zlib-level=6"]
        if sum(entry.size for entry in payload.files) >= GITHUB_ASSET_LIMIT:
            # Create native UDIF segments directly, avoiding a second complete
            # compressed copy while a multi-gigabyte monolithic image exists.
            command.extend(["-segmentSize", "1800m"])
        _command([*command, str(image)])
        _command(["/usr/bin/hdiutil", "verify", str(image)])
    parts = sorted(path for path in destination.iterdir() if path.suffix in {".dmg", ".dmgpart"})
    if not parts or sum(path.suffix == ".dmg" for path in parts) != 1:
        raise RuntimeError("Yerel DMG taşıma bölümleri doğrulanamadı.")
    files = []
    for part in parts:
        if part.is_symlink() or not 0 < part.stat().st_size < GITHUB_ASSET_LIMIT:
            raise RuntimeError("Dağıtım dosyası GitHub tek dosya sınırını aşıyor.")
        files.append({"filename": part.name, "size": part.stat().st_size, "sha256": digest_file(part)})
    report = {"schema_version": 1, "product": "KSI Local Studio", "version": version,
        "architecture": architecture, "transport": "segmented-udif" if len(parts) > 1 else "udif",
        "models_included": True, "notarized": False, "app_signing": "ad-hoc",
        "installation_tested": False, "files": files,
        "offline_manifest_sha256": digest_file(resources / "offline-manifest.json")}
    atomic_write_json(destination / "transport.json", report)
    atomic_write_text(destination / "SHA256SUMS.txt", "\n".join(f"{item['sha256']}  {item['filename']}" for item in files) + "\n")
    if len(parts) > 1:
        atomic_write_text(destination / "Kurulum.txt", "Bütün .dmg ve .dmgpart dosyalarını aynı klasöre indirin.\n"
            "İlk .dmg dosyasını açın; diğer parçalar otomatik olarak okunur.\n"
            "KSI Local Studio’yu Applications’a sürükleyin. İnternet gerekmez.\n"
            "Bu taşıma düzeninin gerçek temiz-Mac kurulum testi yayımdan önce tamamlanmalıdır.\n")
    return report
