"""Build and audit a new public source tree without touching user data."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ksi_local.atomic_files import atomic_write_json


ROOT_FILES = (
    ".gitignore",
    "AGENTS.md",
    "CLAUDE.md",
    "GEMINI.md",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "MODEL_LICENSES.md",
    "NOTICE",
    "RELEASE_NOTES.md",
    "SECURITY.md",
    "SUPPORT.md",
    "THIRD_PARTY_NOTICES.md",
    "pyproject.toml",
)
PUBLIC_DIRECTORIES = (
    ".github",
    "assets",
    "docs/assets",
    "docs/decisions",
    "docs/mcp",
    "native",
    "packaging",
    "src/ksi_local",
    "tests",
)
PUBLIC_DOCUMENTS = (
    "docs/AI_CONTRIBUTOR_GUIDE.md",
    "docs/ARCHITECTURE.md",
    "docs/GLOSSARY.md",
    "docs/KULLANIM_VE_SINIRLAR.md",
    "docs/migration-from-predecessor.md",
)
PUBLIC_SCRIPTS = (
    "scripts/audit_github_artifacts.py",
    "scripts/cleanup_obsolete_stages.py",
    "scripts/build_app_icon.sh",
    "scripts/build_ocr_helper.sh",
    "scripts/build_personal_dmg.sh",
    "scripts/build_public_source.py",
    "scripts/install_macos_app.sh",
    "scripts/notarize_release.sh",
    "scripts/seal_offline_payload.py",
    "scripts/build_offline_dmg.py",
    "scripts/fetch_build_input.py",
    "scripts/assemble_clean_runtime.py",
    "scripts/assemble_offline_app.py",
    "scripts/bootstrap_build_environment.py",
    "scripts/collect_native_notices.py",
    "scripts/collect_wheel_notices.py",
    "scripts/collect_source_license_texts.py",
    "scripts/stage_python_notices.py",
    "scripts/stage_offline_models.py",
    "scripts/build_model_notice_bindings.py",
    "scripts/merge_offline_components.py",
    "scripts/inventory_native_attribution.py",
    "scripts/stage_native_library_components.py",
    "scripts/fetch_corresponding_source.py",
    "scripts/fetch_ollama_models.py",
    "scripts/import_native_artifact.py",
    "scripts/import_speech_artifact.py",
    "scripts/lock_native_libraries.py",
    "scripts/lock_python_wheels.py",
    "scripts/prepare_native_component.py",
    "scripts/resolve_engine_wheels.py",
    "scripts/resolve_native_packages.py",
    "scripts/review_model_input.py",
    "scripts/select_wheelhouse.py",
    "scripts/stage_chatterbox_source_runtime.py",
    "scripts/stage_argos_model.py",
    "scripts/stage_deno_input.py",
    "scripts/stage_ollama_input.py",
    "scripts/snapshot_model_terms.py",
    "scripts/fetch_qt_sources.py",
    "scripts/fetch_python_notice_sources.py",
    "scripts/stage_native_engines.py",
    "scripts/stage_portable_font.py",
)
ALLOWED_BINARY_SUFFIXES = {".png", ".gif", ".icns"}
BLOCKED_PARTS = {
    ".git",
    ".phase1",
    ".pytest_cache",
    ".venv",
    ".venv-chatterbox",
    "__pycache__",
    "backups",
    "build",
    "deployment",
    "dist",
    "jobs",
    "models",
    "outputs",
    "runtime",
}
BLOCKED_SUFFIXES = {
    ".app",
    ".cookies",
    ".db",
    ".dmg",
    ".key",
    ".log",
    ".mobileprovision",
    ".p12",
    ".pem",
    ".pkg",
    ".secret",
    ".sqlite",
    ".sqlite3",
    ".token",
}
TEXT_SUFFIXES = {
    "",
    ".cfg",
    ".ini",
    ".json",
    ".md",
    ".plist",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
    ".example",
    ".spec",
    ".srt",
    ".swift",
}
SECRET_PATTERNS = (
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----")),
    ("github-token", re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{20,}\b")),
    ("openai-key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("aws-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("credential-url", re.compile(r"https?://[^\s/:]+:[^\s/@]+@")),
    (
        "user-home",
        re.compile(
            r"/" + r"Users/(?!you(?:/|\b)|USER(?:/|\b)|\$USER(?:/|\b))[^/\s]+/"
        ),
    ),
    ("literal-disk-uuid", re.compile(r'(?i)"volume_uuid"\s*:\s*"(?!volume-123)[^"{][^"]+"')),
    ("literal-workspace-id", re.compile(r'(?i)"workspace_id"\s*:\s*"(?!workspace-123)[^"{][^"]+"')),
)
PREDECESSOR_ALLOWLIST = {
    "docs/migration-from-predecessor.md",
    "src/ksi_local/migration.py",
    "tests/test_phase22_identity.py",
}
SYNTHETIC_TEST_ALLOWLIST = {
    ("tests/test_downloader.py", "credential-url"),
    ("tests/test_wheel_lock.py", "credential-url"),  # Literal user:password rejection fixture.
    ("tests/test_phase25.py", "credential-url"),
    ("tests/test_phase29.py", "credential-url"),
    ("tests/test_phase36.py", "openai-key"),
    ("tests/test_phase4.py", "user-home"),
    ("tests/test_phase22_identity.py", "literal-workspace-id"),
}


@dataclass(frozen=True)
class AuditFinding:
    path: str
    rule: str
    line: int | None = None


@dataclass(frozen=True)
class PublicTreeReport:
    destination: str
    file_count: int
    total_bytes: int
    manifest_sha256: str
    findings: tuple[AuditFinding, ...]

    @property
    def passed(self) -> bool:
        return not self.findings


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _iter_public_inputs(source: Path) -> Iterable[tuple[Path, Path]]:
    for relative in (*ROOT_FILES, *PUBLIC_DOCUMENTS, *PUBLIC_SCRIPTS):
        path = source / relative
        if path.is_file():
            yield path, Path(relative)
    public_readme = source / "docs/public/README.md"
    if not public_readme.is_file():
        public_readme = source / "README.md"
    if public_readme.is_file():
        yield public_readme, Path("README.md")
    for directory in PUBLIC_DIRECTORIES:
        root = source / directory
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(source)
            if not path.is_file() or path.is_symlink():
                continue
            if any(part in BLOCKED_PARTS or part == "manual" for part in relative.parts):
                continue
            if path.suffix in {".pyc", ".pyo"} or ".egg-info" in relative.parts:
                continue
            yield path, relative
    for relative in ("config/glossary.json", "config/public-catalog.json", "config/runtime-sources.json", "config/native-sources.json", "config/model-sources.json", "config/ollama-model-sources.json", "config/native-libraries-arm64.json", "config/native-libraries-x86_64.json", "config/python-wheels-arm64.json", "config/python-wheels-x86_64.json", "config/python-build-wheels-arm64.json", "config/python-build-wheels-x86_64.json", "config/python-piper-wheels-arm64.json", "config/python-piper-wheels-x86_64.json", "config/python-chatterbox-wheels-arm64.json"):
        path = source / relative
        if path.is_file():
            yield path, Path(relative)
    qt_sources = source / "config/qt-corresponding-sources.json"
    if qt_sources.is_file():
        yield qt_sources, Path("config/qt-corresponding-sources.json")
    python_notices = source / "config/python-notice-sources.json"
    if python_notices.is_file():
        yield python_notices, Path("config/python-notice-sources.json")
    model_notices = source / "config/model-notice-snapshots.json"
    if model_notices.is_file():
        yield model_notices, Path("config/model-notice-snapshots.json")
    native_sources = source / "config/native-corresponding-sources.json"
    if native_sources.is_file():
        yield native_sources, Path("config/native-corresponding-sources.json")


def _sanitized_configuration(source: Path, destination: Path) -> None:
    tool_manifest = json.loads((source / "config/tool-manifest.json").read_text(encoding="utf-8"))
    for tool in tool_manifest.get("tools", {}).values():
        if isinstance(tool, dict):
            tool.pop("path", None)
            tool.pop("environment", None)
            if "installed" in tool:
                tool["installed"] = False
            tool.pop("installed_on_external_ssd", None)
            if "integrity_verified" in tool:
                tool["integrity_verified"] = False
            tool.pop("server_running_during_inventory", None)
    for model in tool_manifest.get("models", {}).values():
        if isinstance(model, dict):
            model.pop("installed_on_external_ssd", None)
            model["integrity_verified"] = False
    tool_manifest["recorded_at"] = None
    tool_manifest["public_template"] = True
    atomic_write_json(destination / "config/tool-manifest.json", tool_manifest, mode=0o644)

    voice = json.loads((source / "config/voice-profile.json").read_text(encoding="utf-8"))
    voice["status"] = "template"
    voice["accepted_at"] = None
    voice["runtime"].pop("environment", None)
    voice.pop("user_evaluation", None)
    voice["voice"]["reference_audio"] = None
    voice["voice"]["type"] = "builtin_synthetic"
    atomic_write_json(destination / "config/voice-profile.json", voice, mode=0o644)


def _spdx_document(root: Path, manifest: list[dict[str, object]]) -> dict[str, object]:
    namespace_hash = hashlib.sha256(
        "\n".join(str(item["sha256"]) for item in manifest).encode("ascii")
    ).hexdigest()
    packages = [
        {
            "SPDXID": "SPDXRef-Package-KSI",
            "name": "ksi-local-studio",
            "versionInfo": "2.0.0.dev0",
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": True,
            "licenseConcluded": "Apache-2.0",
            "licenseDeclared": "Apache-2.0",
            "copyrightText": "Copyright 2026 KSI Local Studio contributors",
        }
    ]
    dependency_data = (
        ("lingua-language-detector", "2", "MIT"),
        ("pypdf", "6", "BSD-3-Clause"),
        ("python-docx", "1.2", "MIT"),
        ("Pillow", "12", "HPND"),
        ("numpy", "2", "BSD-3-Clause"),
        ("PySide6", "6.8", "LGPL-3.0-only OR GPL-3.0-only OR LicenseRef-Commercial"),
        ("mlx-whisper", "0.4", "MIT"),
    )
    relationships: list[dict[str, str]] = []
    # This inventories exact declared build inputs, not successful installation
    # or completed redistribution review. Binary SBOM remains a separate gate.
    locked = []
    for filename in ("python-wheels-arm64.json", "python-wheels-x86_64.json",
                     "python-piper-wheels-arm64.json", "python-piper-wheels-x86_64.json",
                     "python-chatterbox-wheels-arm64.json"):
        lock_path = root / "config" / filename
        if lock_path.is_file():
            from ksi_local.wheel_lock import validate_wheel_lock
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
            locked.extend((row, filename) for row in validate_wheel_lock(lock))
    if locked:
        dependency_data = ()
    for index, (name, version, license_id) in enumerate(dependency_data, start=1):
        package_id = f"SPDXRef-Dependency-{index}"
        packages.append(
            {
                "SPDXID": package_id,
                "name": name,
                "versionInfo": version,
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": license_id,
                "copyrightText": "NOASSERTION",
            }
        )
        relationships.append(
            {
                "spdxElementId": "SPDXRef-Package-KSI",
                "relationshipType": "DEPENDS_ON",
                "relatedSpdxElement": package_id,
            }
        )
    seen = set()
    for row, filename in locked:
        identity = (row["name"], row["version"], row["sha256"])
        if identity in seen:
            continue
        seen.add(identity)
        package_id = f"SPDXRef-LockedWheel-{len(seen)}"
        packages.append({"SPDXID": package_id, "name": row["name"],
            "versionInfo": row["version"], "downloadLocation": row["url"],
            "filesAnalyzed": False, "licenseConcluded": "NOASSERTION",
            "licenseDeclared": row.get("license", "NOASSERTION"),
            "copyrightText": "NOASSERTION", "checksums": [{"algorithm": "SHA256", "checksumValue": row["sha256"]}],
            "comment": f"Declared public build input from {filename}; installed speech code may have an explicit pinned Git source override. Not binary acceptance or license approval."})
        relationships.append({"spdxElementId": "SPDXRef-Package-KSI", "relationshipType": "DEPENDS_ON", "relatedSpdxElement": package_id})
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": "KSI Local Studio source release",
        "documentNamespace": f"https://ksi.local/spdx/{namespace_hash}",
        "creationInfo": {"created": "2026-10-01T00:00:00Z", "creators": ["Tool: ksi-release-prep-1"]},
        "documentDescribes": ["SPDXRef-Package-KSI"],
        "packages": packages,
        "relationships": relationships,
        "annotations": [{"annotationType": "OTHER", "annotator": "Tool: ksi-release-prep-1", "annotationDate": "2026-10-01T00:00:00Z", "comment": f"Source-tree SBOM; {len(manifest)} hashed files. Binary release SBOM must be regenerated from the final package."}],
    }


def audit_public_tree(root: str | Path) -> tuple[AuditFinding, ...]:
    base = Path(root).resolve()
    findings: list[AuditFinding] = []
    for path in sorted(base.rglob("*")):
        relative = path.relative_to(base)
        name = str(relative)
        if ".git" in relative.parts:
            continue
        if path.is_symlink():
            findings.append(AuditFinding(name, "symlink"))
            continue
        if path.is_dir():
            if any(part in BLOCKED_PARTS for part in relative.parts):
                findings.append(AuditFinding(name, "blocked-directory"))
            continue
        if path.suffix.casefold() in BLOCKED_SUFFIXES or path.name.startswith(".env"):
            findings.append(AuditFinding(name, "blocked-file"))
            continue
        if path.suffix.casefold() not in TEXT_SUFFIXES | ALLOWED_BINARY_SUFFIXES:
            findings.append(AuditFinding(name, "unapproved-binary"))
            continue
        if path.suffix.casefold() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            findings.append(AuditFinding(name, "non-utf8-text"))
            continue
        for rule, pattern in SECRET_PATTERNS:
            for match in pattern.finditer(text):
                if (name, rule) in SYNTHETIC_TEST_ALLOWLIST:
                    continue
                findings.append(AuditFinding(name, rule, text.count("\n", 0, match.start()) + 1))
        if "Video" + "TR" in text and name not in PREDECESSOR_ALLOWLIST:
            findings.append(AuditFinding(name, "private-predecessor-name"))
    required = {"LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md", "MODEL_LICENSES.md", "SBOM.spdx.json"}
    for missing in sorted(required - {str(item.relative_to(base)) for item in base.rglob("*") if item.is_file()}):
        findings.append(AuditFinding(missing, "required-release-file-missing"))
    return tuple(findings)


def audit_git_history(repository: str | Path) -> tuple[AuditFinding, ...]:
    """Scan every committed blob; working-tree cleanliness is checked separately."""
    root = Path(repository).expanduser().resolve()
    if not (root / ".git").is_dir():
        raise ValueError("Denetlenecek temiz public Git deposu bulunamadı.")
    revisions = subprocess.run(
        ["git", "rev-list", "--all"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.splitlines()
    findings: list[AuditFinding] = []
    for revision in revisions:
        listing = subprocess.run(
            ["git", "ls-tree", "-r", "-z", revision],
            cwd=root,
            check=True,
            capture_output=True,
            timeout=30,
        ).stdout
        present: set[str] = set()
        for row in listing.split(b"\0"):
            if not row:
                continue
            metadata, raw_name = row.split(b"\t", 1)
            mode, kind, object_id = metadata.decode("ascii").split()
            name = raw_name.decode("utf-8")
            present.add(name)
            path = Path(name)
            if mode == "120000":
                findings.append(AuditFinding(f"{revision[:12]}:{name}", "symlink"))
                continue
            if kind != "blob":
                continue
            if path.suffix.casefold() in BLOCKED_SUFFIXES or path.name.startswith(".env"):
                findings.append(AuditFinding(f"{revision[:12]}:{name}", "blocked-file"))
                continue
            if path.suffix.casefold() not in TEXT_SUFFIXES | ALLOWED_BINARY_SUFFIXES:
                findings.append(AuditFinding(f"{revision[:12]}:{name}", "unapproved-binary"))
                continue
            if path.suffix.casefold() not in TEXT_SUFFIXES:
                continue
            content = subprocess.run(
                ["git", "cat-file", "blob", object_id],
                cwd=root,
                check=True,
                capture_output=True,
                timeout=30,
            ).stdout
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                findings.append(AuditFinding(f"{revision[:12]}:{name}", "non-utf8-text"))
                continue
            for rule, pattern in SECRET_PATTERNS:
                for match in pattern.finditer(text):
                    if (name, rule) in SYNTHETIC_TEST_ALLOWLIST:
                        continue
                    findings.append(
                        AuditFinding(
                            f"{revision[:12]}:{name}",
                            rule,
                            text.count("\n", 0, match.start()) + 1,
                        )
                    )
            if "Video" + "TR" in text and name not in PREDECESSOR_ALLOWLIST:
                findings.append(
                    AuditFinding(f"{revision[:12]}:{name}", "private-predecessor-name")
                )
        required = {
            "LICENSE",
            "NOTICE",
            "THIRD_PARTY_NOTICES.md",
            "MODEL_LICENSES.md",
            "SBOM.spdx.json",
        }
        for missing in sorted(required - present):
            findings.append(
                AuditFinding(f"{revision[:12]}:{missing}", "required-release-file-missing")
            )
    return tuple(findings)


def build_public_tree(source: str | Path, destination: str | Path) -> PublicTreeReport:
    raw_origin = Path(source).expanduser()
    raw_target = Path(destination).expanduser()
    if raw_origin.is_symlink():
        raise ValueError("Public kaynak kökü sembolik bağlantı olamaz.")
    if raw_target.is_symlink():
        raise FileExistsError("Public kaynak hedefi yeni ve boş olmalıdır.")
    origin = raw_origin.resolve()
    target = raw_target.resolve()
    if target.exists():
        raise FileExistsError("Public kaynak hedefi yeni ve boş olmalıdır.")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.", suffix=".part", dir=target.parent))
    try:
        for path, relative in _iter_public_inputs(origin):
            output = staging / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, output, follow_symlinks=False)
            output.chmod(0o755 if os.access(path, os.X_OK) and path.suffix in {"", ".sh"} else 0o644)
        _sanitized_configuration(origin, staging)
        manifest: list[dict[str, object]] = []
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                manifest.append({"path": str(path.relative_to(staging)), "size_bytes": path.stat().st_size, "sha256": _sha256(path)})
        atomic_write_json(staging / "SBOM.spdx.json", _spdx_document(staging, manifest), mode=0o644)
        manifest.append({"path": "SBOM.spdx.json", "size_bytes": (staging / "SBOM.spdx.json").stat().st_size, "sha256": _sha256(staging / "SBOM.spdx.json")})
        atomic_write_json(staging / "SOURCE-MANIFEST.json", {"schema_version": 1, "files": manifest}, mode=0o644)
        findings = audit_public_tree(staging)
        if findings:
            labels = ", ".join(f"{item.path}:{item.rule}" for item in findings[:8])
            raise RuntimeError(f"Public kaynak denetimi başarısız: {labels}")
        os.replace(staging, target)
        manifest_hash = _sha256(target / "SOURCE-MANIFEST.json")
        return PublicTreeReport(str(target), len(manifest) + 1, sum(path.stat().st_size for path in target.rglob("*") if path.is_file()), manifest_hash, ())
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
