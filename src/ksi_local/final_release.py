"""Non-destructive Phase 40 cleanup preview and final release gate."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from ksi_local import __version__
from ksi_local.atomic_files import atomic_write_json
from ksi_local.manual_acceptance import progress_dict
from ksi_local.project_metadata import PRODUCT_NAME
from ksi_local.release_verification import (
    has_apple_notarization,
    has_developer_id_signature,
)


_CACHE_DIRECTORY_NAMES = frozenset({"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"})
_FAILED_PILOT_NAMES = frozenset(
    {"failed-pilots", "pilot-failures", ".failed-pilots", "phase19_pilot.app"}
)
_PROTECTED_ROOTS = (
    "src",
    "tests",
    "docs",
    "config",
    "assets",
    "packaging",
    "scripts",
    ".venv",
    ".venv-chatterbox",
    ".phase1",
)


@dataclass(frozen=True)
class CleanupPreviewItem:
    relative_path: str
    size_bytes: int
    reason: str


@dataclass(frozen=True)
class CleanupPreview:
    product: str
    version: str
    candidates: tuple[CleanupPreviewItem, ...]
    protected: tuple[str, ...]
    total_candidate_bytes: int
    destructive_action_performed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "candidates": [asdict(item) for item in self.candidates],
            "protected": list(self.protected),
        }


@dataclass(frozen=True)
class ReleaseGate:
    product: str
    version: str
    ready_to_publish: bool
    checks: dict[str, bool]
    blockers: tuple[str, ...]
    package_sha256: str | None
    package_size_bytes: int | None

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "blockers": list(self.blockers)}


def _directory_size(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        if item.is_file() and not item.is_symlink():
            try:
                total += item.stat().st_size
            except OSError:
                continue
    return total


def _candidate_directories(root: Path) -> Iterable[tuple[Path, str]]:
    for path in root.rglob("*"):
        if not path.is_dir() or path.is_symlink():
            continue
        relative = path.relative_to(root)
        if any(part in {".git", ".venv", ".venv-chatterbox"} for part in relative.parts):
            continue
        if path.name in _CACHE_DIRECTORY_NAMES:
            yield path, "Yeniden üretilebilir geliştirme önbelleği"
        elif path.name in _FAILED_PILOT_NAMES:
            yield path, "Başarısız ve yeniden çalıştırılabilir pilot çıktısı"


def build_cleanup_preview(project_root: str | Path) -> CleanupPreview:
    """Inventory safe candidates without deleting, moving, or modifying any file."""
    unresolved = Path(project_root).expanduser()
    if unresolved.is_symlink():
        raise ValueError("KSI Local Studio proje kökü geçerli bir klasör olmalıdır.")
    root = unresolved.resolve()
    if not root.is_dir():
        raise ValueError("KSI Local Studio proje kökü geçerli bir klasör olmalıdır.")
    if not (root / "pyproject.toml").is_file() or not (root / "src/ksi_local").is_dir():
        raise ValueError("Seçilen klasör KSI Local Studio kaynak ağacı değildir.")

    found: dict[str, CleanupPreviewItem] = {}
    for path, reason in _candidate_directories(root):
        relative = str(path.relative_to(root))
        # A parent cache candidate already accounts for all descendants.
        if any(relative.startswith(existing + "/") for existing in found):
            continue
        found[relative] = CleanupPreviewItem(relative, _directory_size(path), reason)

    for path in sorted(root.glob(".env*")):
        if path.name == ".env.example" or path.is_symlink() or not path.is_file():
            continue
        found[path.name] = CleanupPreviewItem(
            path.name,
            path.stat().st_size,
            "Git dışı gizli ortam dökümü; içerik önizlemeye alınmadı",
        )

    build_root = root / "build"
    if build_root.is_dir() and not build_root.is_symlink():
        found["build"] = CleanupPreviewItem(
            "build", _directory_size(build_root), "Kurulumdan önce yeniden üretilebilen build çıktısı"
        )

    dist_root = root / "dist"
    if dist_root.is_dir() and not dist_root.is_symlink():
        current_prefix = f"KSI Local Studio-{__version__}-"
        for path in sorted(dist_root.iterdir()):
            if path.name.startswith(current_prefix) or path.name == "phase40-cleanup-preview.json":
                continue
            if path.is_symlink() or not (path.is_file() or path.is_dir()):
                continue
            if path.suffix.casefold() not in {".dmg", ".app", ".sha256"}:
                continue
            relative = str(path.relative_to(root))
            size = _directory_size(path) if path.is_dir() else path.stat().st_size
            found[relative] = CleanupPreviewItem(
                relative, size, "Geçerli KSI sürümü olmayan eski dağıtım çıktısı"
            )

    # A broad candidate such as ``build`` already accounts for every cache
    # below it.  Keep only the outermost candidate so the preview never
    # overstates reclaimable space or asks the user to approve the same bytes
    # twice.
    outermost: dict[str, CleanupPreviewItem] = {}
    for relative, item in sorted(
        found.items(), key=lambda pair: (len(Path(pair[0]).parts), pair[0])
    ):
        candidate = Path(relative)
        if any(candidate.is_relative_to(Path(parent)) for parent in outermost):
            continue
        outermost[relative] = item

    candidates = tuple(sorted(outermost.values(), key=lambda item: item.relative_path))
    protected = (
        *(_PROTECTED_ROOTS),
        "KSI-Workspace içindeki tüm kaynaklar, işler ve tamamlanmış çıktılar",
        "Application Support içindeki tercihler, kabul kaydı ve private katalog",
        "Kullanıcının kabul ettiği yerel modeller",
        "Geçerli sürüm paketi ve checksum dosyaları",
    )
    return CleanupPreview(
        product=PRODUCT_NAME,
        version=__version__,
        candidates=candidates,
        protected=protected,
        total_candidate_bytes=sum(item.size_bytes for item in candidates),
    )


def write_cleanup_preview(project_root: str | Path, output: str | Path) -> CleanupPreview:
    preview = build_cleanup_preview(project_root)
    atomic_write_json(Path(output).expanduser().resolve(), preview.to_dict(), mode=0o600)
    return preview


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def release_source_sha256(project_root: str | Path) -> str:
    """Fingerprint release-relevant source without including machine or user state."""
    unresolved = Path(project_root).expanduser()
    if unresolved.is_symlink():
        raise ValueError("Sürüm kaynak kökü sembolik bağlantı olamaz.")
    root = unresolved.resolve()
    selected: list[Path] = []
    for relative in ("src/ksi_local", "native", "packaging", "scripts", "assets"):
        directory = root / relative
        if directory.is_dir():
            selected.extend(
                path
                for path in directory.rglob("*")
                if path.is_file()
                and not path.is_symlink()
                and "__pycache__" not in path.parts
                and path.suffix not in {".pyc", ".pyo"}
            )
    for relative in (
        "pyproject.toml",
        "config/glossary.json",
        "config/public-catalog.json",
        "config/tool-manifest.json",
        "config/voice-profile.json",
    ):
        path = root / relative
        if path.is_file() and not path.is_symlink():
            selected.append(path)
    if not selected:
        raise ValueError("Sürüm kaynak parmak izi için dosya bulunamadı.")
    digest = hashlib.sha256()
    for path in sorted(set(selected), key=lambda item: str(item.relative_to(root))):
        relative = str(path.relative_to(root)).encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(_sha256(path)))
    return digest.hexdigest()


def evaluate_release_gate(
    *,
    acceptance_session: str | Path,
    public_tree_findings: int,
    public_history_findings: int,
    package_path: str | Path | None = None,
    package_manifest_path: str | Path | None = None,
    clean_install_accepted: bool = False,
    cleanup_approved_and_completed: bool = False,
    project_root: str | Path | None = None,
    signature_verifier=has_developer_id_signature,
    notarization_verifier=has_apple_notarization,
) -> ReleaseGate:
    """Combine release evidence; never publishes, notarizes, installs, or cleans."""
    acceptance = progress_dict(acceptance_session)
    raw_package = Path(package_path).expanduser() if package_path else None
    raw_manifest = Path(package_manifest_path).expanduser() if package_manifest_path else None
    package = raw_package.resolve() if raw_package and not raw_package.is_symlink() else None
    manifest = raw_manifest.resolve() if raw_manifest and not raw_manifest.is_symlink() else None
    package_digest: str | None = None
    package_size: int | None = None
    package_valid = False
    notarized = False
    developer_id = False
    source_current = False
    if package and manifest and package.is_file() and manifest.is_file():
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            package_digest = _sha256(package)
            package_size = package.stat().st_size
            package_valid = (
                payload.get("product") == PRODUCT_NAME
                and payload.get("version") == __version__
                and payload.get("package_filename") == package.name
                and payload.get("sha256") == package_digest
                and payload.get("size_bytes") == package_size
            )
            developer_id = (
                package_valid
                and payload.get("signing") == "developer-id"
                and bool(signature_verifier(package))
            )
            notarized = (
                package_valid
                and payload.get("notarized") is True
                and bool(notarization_verifier(package))
            )
            if project_root is not None:
                source_current = payload.get("source_sha256") == release_source_sha256(project_root)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            package_valid = False

    checks = {
        "manual_acceptance": acceptance["releasable"] is True,
        "clean_install_acceptance": clean_install_accepted,
        "cleanup_approved_and_completed": cleanup_approved_and_completed,
        "public_tree_audit": public_tree_findings == 0,
        "public_history_audit": public_history_findings == 0,
        "package_checksum": package_valid,
        "package_matches_current_source": source_current,
        "developer_id_signature": developer_id,
        "apple_notarization": notarized,
    }
    blocker_labels = {
        "manual_acceptance": "Faz 38 insan kabul matrisi tamamlanmadı veya kalite kapısını geçmedi.",
        "clean_install_acceptance": "Temiz Mac kurulum kabulü tamamlanmadı.",
        "cleanup_approved_and_completed": "Temizlik önizlemesi kullanıcı tarafından onaylanıp uygulanmadı.",
        "public_tree_audit": "Public kaynak ağacı denetiminde bulgu var.",
        "public_history_audit": "Public Git geçmişi denetiminde bulgu var.",
        "package_checksum": "Güncel paket ile release manifesti eşleşmiyor.",
        "package_matches_current_source": "Paket mevcut kaynak ağacından üretilmedi.",
        "developer_id_signature": "Paket Developer ID ile imzalanmadı.",
        "apple_notarization": "Paket Apple tarafından notarize edilmedi.",
    }
    blockers = tuple(blocker_labels[key] for key, passed in checks.items() if not passed)
    return ReleaseGate(
        product=PRODUCT_NAME,
        version=__version__,
        ready_to_publish=all(checks.values()),
        checks=checks,
        blockers=blockers,
        package_sha256=package_digest,
        package_size_bytes=package_size,
    )
