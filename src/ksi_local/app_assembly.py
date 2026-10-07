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
        source = safe_member(repository, name)
        target = resources / "runtime" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise ValueError("Source would replace a runtime member.")
        shutil.copy2(source, target)
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
            isolated_lock = json.loads((repository / f"config/python-{scope}-wheels-{architecture}.json").read_text())
            validate_notice_inventory(resources, spec, isolated_lock, scope=scope)
    launcher = contents / "MacOS/KSI-Local-Studio"
    shutil.copy2(repository / "packaging/KSI-Local-Studio-portable-launcher", launcher)
    launcher.chmod(0o755)
    shutil.copy2(repository / "packaging/KSI-Local-Studio.icns", resources / "KSI-Local-Studio.icns")
    info = plistlib.loads((repository / "packaging/Info.plist").read_bytes())
    info.update(KSIArchitecture=architecture, KSISourceCommit=commit,
                LSArchitecturePriority=[architecture], CFBundleVersion="200")
    atomic_write_bytes(contents / "Info.plist", plistlib.dumps(info), mode=0o644)
    atomic_write_json(resources / "build-provenance.json", {"schema_version": 1, "source_commit": commit, "architecture": architecture, "signing": "adhoc", "acceptance_tested": False, "wheel_packages": len(rows)}, mode=0o644)
    component_records = [{key: row[key] for key in ("path", "identifier", "role", "sha256", "size", "license", "license_file", "source_url", "revision", "corresponding_source") if key in row} for row in spec["files"]]
    atomic_write_json(resources / "component-provenance.json", {"schema_version": 1, "architecture": architecture, "files": component_records}, mode=0o644)
    # Sign inner Mach-O files first. Their post-signing digests, not the original
    # downloaded binary digests, belong in the final runtime integrity manifest.
    for path in sorted(resources.rglob("*")):
        if not path.is_file():
            continue
        with path.open("rb") as stream:
            magic = stream.read(4)
        if magic in {b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xce", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"}:
            subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", str(path)], check=True, capture_output=True, timeout=120)
    native_inputs = json.loads((repository / "config/native-sources.json").read_text())["inputs"]
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
    return dict(sealed, source_commit=commit, acceptance_tested=False)
