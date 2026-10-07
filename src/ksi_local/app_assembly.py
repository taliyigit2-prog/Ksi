"""Clean app assembly from explicit runtime, component locks and committed source.

No user workspace is discoverable through this builder. Acceptance and release
publication are separate steps and cannot be inferred from a successful build.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import plistlib
import re
import shutil
import subprocess
from pathlib import Path

from ksi_local.atomic_files import atomic_write_bytes, atomic_write_json
from ksi_local.bundle_runtime import digest_file, host_architecture, safe_member
from ksi_local.copy_on_write import clone_file
from ksi_local.offline_build import seal_offline_payload
from ksi_local.wheel_lock import validate_wheel_lock
from ksi_local.distribution_notices import validate_notice_inventory
from ksi_local.native_processor import require_native_build_process


_MACHO_MAGICS = {b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xce",
    b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"}


def committed_file(repository: Path, commit: str, name: str) -> tuple[bytes, int]:
    """Read bounded ordinary Git blobs, never mutable working-tree content."""
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("App source needs an immutable full Git commit.")
    safe_member(repository, name)
    listing = subprocess.run(["git", "ls-tree", "-z", commit, "--", name], cwd=repository,
        check=True, capture_output=True, timeout=30).stdout.split(b"\0")
    listing = [row for row in listing if row]
    if len(listing) != 1:
        raise ValueError("App source is missing or is not one ordinary committed file.")
    description, filename = listing[0].split(b"\t", 1)
    mode, kind, blob = description.decode("ascii").split()
    if filename.decode("utf-8") != name or mode not in {"100644", "100755"} or kind != "blob" or not re.fullmatch(r"[0-9a-f]{40}", blob):
        raise ValueError("App source is linked, ambiguous or not an ordinary Git blob.")
    size = int(subprocess.run(["git", "cat-file", "-s", blob], cwd=repository,
        check=True, capture_output=True, timeout=30).stdout)
    if not 0 <= size <= 16 * 1024**2:
        raise ValueError("Committed app source exceeds its file-size bound.")
    content = subprocess.run(["git", "cat-file", "blob", blob], cwd=repository,
        check=True, capture_output=True, timeout=30).stdout
    if len(content) != size:
        raise ValueError("Committed app source is incomplete.")
    return content, 0o755 if mode == "100755" else 0o644


def sign_native_payload(resources: Path, architecture: str) -> int:
    """Check every actual native member before signing any staged binary."""
    if architecture not in {"arm64", "x86_64"} or resources.is_symlink() or not resources.is_dir():
        raise ValueError("Native payload architecture/root is invalid.")
    binaries = []
    for path in sorted(resources.rglob("*")):
        if path.is_symlink():
            raise ValueError("Native payload cannot contain unresolved symbolic links.")
        if not path.is_file():
            continue
        with path.open("rb") as stream:
            magic = stream.read(4)
        if magic not in _MACHO_MAGICS:
            continue
        actual = subprocess.run(["/usr/bin/lipo", "-archs", str(path)], check=True,
            capture_output=True, text=True, timeout=30).stdout.split()
        if architecture not in actual:
            raise ValueError("Native payload member lacks its claimed architecture: " + path.relative_to(resources).as_posix())
        binaries.append(path)
    if not binaries:
        raise ValueError("Native payload contains no actual Mach-O runtime.")
    for path in binaries:
        subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", str(path)],
            check=True, capture_output=True, timeout=120)
    # Signing only a framework's Mach-O executable does not sign the framework
    # bundle itself. Seal code-bearing nested bundles bottom-up before payload
    # hashes are generated; do not let the outer app's signing mutate them.
    bundles = [path for path in (resources, *resources.rglob("*"))
        if path.is_dir() and path.suffix.casefold() in {".framework", ".app", ".bundle", ".plugin"}
        and any(binary.is_relative_to(path) for binary in binaries)]
    for bundle in sorted(bundles, key=lambda path: (len(path.parts), str(path)), reverse=True):
        subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", str(bundle)],
            check=True, capture_output=True, timeout=120)
        subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(bundle)],
            check=True, capture_output=True, timeout=120)
    return len(binaries)


def copy_clean_tree(source: Path, destination: Path) -> None:
    """Materialize only internal symlinks and keep APFS clones independent."""
    if source.is_symlink() or not source.is_dir() or destination.exists():
        raise ValueError("Clean tree copy requires a normal source and new destination.")
    root = source.resolve()
    count = 0

    def visit(origin: Path, target: Path, ancestors: frozenset[Path]):
        nonlocal count
        resolved = origin.resolve(strict=True)
        if not resolved.is_relative_to(root) or resolved in ancestors:
            raise ValueError("Clean runtime has an external or cyclic symbolic link.")
        count += 1
        if count > 100000:
            raise ValueError("Clean runtime contains too many members.")
        if resolved.is_dir():
            target.mkdir(mode=0o755)
            for child in sorted(resolved.iterdir()):
                if child.name == "__pycache__" or child.suffix in {".pyc", ".pyo"}:
                    continue
                visit(child, target / child.name, ancestors | {resolved})
        elif resolved.is_file():
            if not clone_file(resolved, target):
                shutil.copy2(resolved, target)
            # Artifacts may be read-only in a cache; the copied build must be
            # signable without changing the independent original inode.
            target.chmod(0o755 if os.access(resolved, os.X_OK) else 0o644)
        else:
            raise ValueError("Clean runtime contains a device, socket or FIFO.")

    visit(root, destination, frozenset())


def bind_signed_tool_manifest(resources: Path, specification: dict, *, source_inputs: dict | None = None) -> None:
    """Bind pinned tool versions to actual post-signing files, never cache hashes."""
    target = resources / "runtime/config/tool-manifest.json"
    manifest = json.loads(target.read_text(encoding="utf-8"))
    records = manifest.get("tools", {})
    for name, digest_key in (("yt-dlp", "sha256"), ("deno", "binary_sha256")):
        matches = [row for row in specification["files"] if row.get("role") == "tool" and row.get("identifier") == name]
        if len(matches) != 1 or not isinstance(records.get(name), dict):
            raise ValueError("Signed download tool has no unique pinned manifest record.")
        records[name][digest_key] = digest_file(safe_member(resources, matches[0]["path"]))
    if source_inputs is not None:
        architecture = specification["architecture"]
        deno_input = source_inputs["deno-" + architecture]
        records["deno"].update(architecture=architecture, artifact=Path(deno_input["url"]).name,
            archive_sha256=deno_input["sha256"], version=deno_input["version"])
        records["deno"].pop("verification", None)
        records["deno"]["verification"] = {"source_release_digest_pinned": True,
            "signed_binary_bound_to_payload": True, "acceptance_tested": False}
        records["yt-dlp"].update(version=source_inputs["yt-dlp-macos"]["version"],
            upstream_artifact_sha256=source_inputs["yt-dlp-macos"]["sha256"])
        records["yt-dlp"].pop("verification", None)
        records["yt-dlp"]["verification"] = {"source_release_digest_pinned": True,
            "signed_binary_bound_to_payload": True, "acceptance_tested": False}
        runtime = json.loads((resources / "runtime/runtime-provenance.json").read_text())
        records["python"] = {"architecture": architecture, "version": runtime["python_version"],
            "source_sha256": runtime["python_source_sha256"]}
        supplied = {row["identifier"] for row in specification["files"] if row["role"] == "tool"}
        for name, record in records.items():
            if isinstance(record, dict):
                record["bundled"] = name in supplied or name == "python"
    manifest["platform"] = "macos-" + specification["architecture"]
    atomic_write_json(target, manifest, mode=0o644)


def normalize_build_shebangs(resources: Path, build_roots: tuple[Path, ...]) -> None:
    """Remove build-only interpreter locations from all isolated Python prefixes."""
    prefixes = tuple(str(root.absolute()).encode() for root in build_roots)
    for path in resources.rglob("*"):
        if path.is_symlink() or not path.is_file() or path.is_relative_to(resources / "models"):
            continue
        with path.open("rb") as stream:
            first_line = stream.readline(4096)
        if first_line.startswith(b"#!") and (b"/Users/" in first_line or any(prefix in first_line for prefix in prefixes)):
            if path.stat().st_size > 16 * 1024**2:
                raise ValueError("Generated interpreter script exceeds its size bound.")
            content = path.read_bytes()
            if len(content) > 16 * 1024**2 or b"\n" not in content:
                raise ValueError("Generated interpreter script is oversized or malformed.")
            atomic_write_bytes(path, b"#!/usr/bin/env python3.12\n" + content.split(b"\n", 1)[1], mode=0o755)


def assemble_app(repository: Path, runtime: Path, components: Path, specification: dict,
                 destination: Path, *, wheel_lock: dict) -> dict:
    architecture = specification.get("architecture")
    rows = validate_wheel_lock(wheel_lock)
    if architecture != host_architecture() or wheel_lock["architecture"] != architecture:
        raise ValueError("App assembly must run on its actual native architecture.")
    require_native_build_process()
    if not destination.is_absolute() or destination.suffix != ".app" or destination.exists() or destination.is_symlink():
        raise FileExistsError("The app destination must be a new absolute .app path.")
    if any(path.is_symlink() or not path.is_dir() for path in (repository, runtime, components)):
        raise ValueError("Build inputs must be explicit normal directories.")
    provenance = json.loads((runtime / "runtime-provenance.json").read_text(encoding="utf-8"))
    lock_digest = hashlib.sha256(json.dumps(wheel_lock, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if provenance.get("architecture") != architecture or provenance.get("wheel_lock_sha256") != lock_digest:
        raise ValueError("Runtime provenance does not match its reviewed wheel lock.")
    status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=repository, check=True, capture_output=True, text=True).stdout
    if status.strip():
        raise ValueError("App source must match a clean committed checkpoint.")
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repository, check=True, capture_output=True, text=True).stdout.strip()
    listing = subprocess.run(["git", "ls-files", "-z"], cwd=repository, check=True, capture_output=True).stdout.decode().split("\0")
    approved = []
    for name in listing:
        if name.startswith("src/ksi_local/") or name in {"config/glossary.json", "config/public-catalog.json", "config/tool-manifest.json", "config/voice-profile.json", "assets/ksi-logo.png", "LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md", "MODEL_LICENSES.md"}:
            approved.append(name)
    spec = copy.deepcopy(specification)
    for row in spec.get("files", []):
        path = safe_member(components, row["path"])
        if not path.is_file() or path.stat().st_size != row["size"] or digest_file(path) != row["sha256"]:
            raise ValueError("Explicit component staging differs from its approved input.")
        if row["path"].startswith("runtime/"):
            raise ValueError("Components cannot replace the clean runtime or source.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    contents = destination / "Contents"
    resources = contents / "Resources"
    resources.mkdir(parents=True)
    (contents / "MacOS").mkdir()
    copy_clean_tree(runtime, resources / "runtime")
    for script in (resources / "runtime/python/bin").iterdir():
        if not script.is_file():
            continue
        with script.open("rb") as stream:
            first_line = stream.readline(4096)
        if first_line.startswith(b"#!") and str(runtime).encode() in first_line:
            content = script.read_bytes()
            atomic_write_bytes(script, b"#!/usr/bin/env python3.12\n" + content.split(b"\n", 1)[1], mode=0o755)
    for name in approved:
        target = resources / "runtime" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise ValueError("Source would replace a runtime member.")
        content, mode = committed_file(repository, commit, name)
        atomic_write_bytes(target, content, mode=mode)
    profile = resources / "runtime/config/voice-profile.json"
    voice = json.loads(profile.read_text(encoding="utf-8"))
    voice.update(status="local-default", accepted_at=None)
    voice.pop("user_evaluation", None)
    voice["voice"]["description"] = "Local Turkish narrator · upstream built-in preset"
    atomic_write_json(profile, voice, mode=0o644)
    for row in spec["files"]:
        origin = safe_member(components, row["path"])
        target = safe_member(resources, row["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise FileExistsError("Component staging would overwrite an existing member.")
        if not clone_file(origin, target):
            shutil.copy2(origin, target)
        if target.stat().st_size != row["size"] or digest_file(target) != row["sha256"]:
            raise ValueError("Component changed between input validation and copying.")
        # Isolated runtime support includes executable helpers as well as data.
        # Removing their execute bits breaks subprocess entry points even when
        # the top-level interpreter was declared as a verified tool.
        executable = row["role"] == "tool" or (row["role"] == "support" and os.access(origin, os.X_OK))
        target.chmod(0o755 if executable else 0o644)
    normalize_build_shebangs(resources, (runtime, components, repository.parent))
    validate_notice_inventory(resources, spec, wheel_lock, scope="main")
    isolated_tools = {row["identifier"] for row in spec["files"] if row["role"] == "tool"}
    for scope in ("piper", "chatterbox"):
        if scope + "-python" in isolated_tools:
            isolated_lock = json.loads(committed_file(repository, commit, f"config/python-{scope}-wheels-{architecture}.json")[0])
            source_pins = None
            if scope == "piper":
                notice_sources = json.loads(committed_file(repository, commit, "config/tool-source-notices.json")[0])["sources"]
                source_pins = {name: notice_sources[name]["source_archive_sha256"]
                    for name in ("piper-corresponding-source", "piper-espeak-source")}
            validate_notice_inventory(resources, spec, isolated_lock, scope=scope, corresponding_sources=source_pins)
    launcher = contents / "MacOS/KSI-Local-Studio"
    launcher_bytes, _ = committed_file(repository, commit, "packaging/KSI-Local-Studio-portable-launcher")
    atomic_write_bytes(launcher, launcher_bytes, mode=0o755)
    icon_bytes, _ = committed_file(repository, commit, "packaging/KSI-Local-Studio.icns")
    atomic_write_bytes(resources / "KSI-Local-Studio.icns", icon_bytes, mode=0o644)
    info = plistlib.loads(committed_file(repository, commit, "packaging/Info.plist")[0])
    info.update(KSIArchitecture=architecture, KSISourceCommit=commit,
                LSArchitecturePriority=[architecture], CFBundleVersion="200")
    atomic_write_bytes(contents / "Info.plist", plistlib.dumps(info), mode=0o644)
    atomic_write_json(resources / "build-provenance.json", {"schema_version": 1, "source_commit": commit, "architecture": architecture, "signing": "adhoc", "acceptance_tested": False, "wheel_packages": len(rows)}, mode=0o644)
    component_records = [{key: row[key] for key in ("path", "identifier", "role", "sha256", "size", "license", "license_file", "source_url", "revision", "corresponding_source") if key in row} for row in spec["files"]]
    atomic_write_json(resources / "component-provenance.json", {"schema_version": 1, "architecture": architecture, "files": component_records}, mode=0o644)
    # Sign inner Mach-O files first. Their post-signing digests, not the original
    # downloaded binary digests, belong in the final runtime integrity manifest.
    native_files = sign_native_payload(resources, architecture)
    native_inputs = json.loads(committed_file(repository, commit, "config/native-sources.json")[0])["inputs"]
    bind_signed_tool_manifest(resources, spec, source_inputs=native_inputs)
    represented = {row["path"] for row in spec["files"]}
    for row in spec["files"]:
        path = safe_member(resources, row["path"])
        row.update(build_input_sha256=row["sha256"], sha256=digest_file(path), size=path.stat().st_size)
    for index, path in enumerate(sorted(resources.rglob("*"))):
        if not path.is_file():
            continue
        name = path.relative_to(resources).as_posix()
        if name in represented:
            continue
        identifier = "voice-profile" if path == profile else f"runtime-{index:05d}"
        spec["files"].append({"path": name, "sha256": digest_file(path), "size": path.stat().st_size, "role": "support", "identifier": identifier})
    sealed = seal_offline_payload(resources, spec)
    subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", str(destination)], check=True, capture_output=True, timeout=120)
    subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(destination)], check=True, capture_output=True, timeout=120)
    return dict(sealed, source_commit=commit, native_files=native_files, acceptance_tested=False)
