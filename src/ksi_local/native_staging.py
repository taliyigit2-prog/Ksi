"""Stage only a selected Mach-O dependency graph, never a personal prefix."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from ksi_local.atomic_files import atomic_write_json
from ksi_local.bundle_runtime import digest_file
from ksi_local.copy_on_write import clone_file


def system_dependency(value: str) -> bool:
    return value.startswith(("/usr/lib/", "/System/Library/")) and not any(part in {".", ".."} for part in value.split("/"))


def library_candidate(value: str, *, origin: Path, prefix: Path) -> Path | None:
    if system_dependency(value):
        return None
    if value.startswith("@rpath/"):
        relative = value[len("@rpath/"):]
        if "/" in relative or not re.fullmatch(r"[A-Za-z0-9_.+-]+\.dylib", relative):
            raise ValueError("Native library reference has an unsupported layout.")
        candidate = prefix / "lib" / relative
    elif value.startswith("@loader_path/"):
        candidate = origin.parent / value[len("@loader_path/"):]
    elif value.startswith("/"):
        direct = Path(value)
        # Rebind a recorded vendor build-prefix name only to an explicit clean
        # prefix member. Never read or copy the recorded Homebrew/user path.
        candidate = direct if direct.is_relative_to(prefix) else prefix / "lib" / direct.name
    else:
        raise ValueError("Native library reference is not relocatable.")
    try:
        actual = candidate.resolve(strict=True)
    except FileNotFoundError as error:
        raise ValueError("Required native library is absent from the clean prefix: " + candidate.name) from error
    if not actual.is_relative_to(prefix.resolve()) or not actual.is_file() or actual.suffix != ".dylib":
        raise ValueError("Native library reference escapes its explicit clean prefix: " + value)
    return candidate


def dependencies(path: Path) -> list[str]:
    data = subprocess.run(["/usr/bin/otool", "-L", str(path)], check=True, capture_output=True, text=True, timeout=30).stdout
    if len(data) > 65536:
        raise ValueError("Native dependency listing exceeds the build limit.")
    return [line.strip().split(" (", 1)[0] for line in data.splitlines()[1:] if line.strip()]


def rpaths(path: Path) -> list[str]:
    data = subprocess.run(["/usr/bin/otool", "-l", str(path)], check=True, capture_output=True, text=True, timeout=30).stdout
    if len(data) > 2 * 1024**2:
        raise ValueError("Native load-command listing exceeds the build limit.")
    return re.findall(r"cmd LC_RPATH\s+cmdsize \d+\s+path (.+?) \(offset \d+\)", data)


def staged_dependency(value: str, *, origin: Path, root: Path) -> Path | None:
    """Resolve final load commands without any system or build-prefix fallback."""
    if system_dependency(value):
        return None
    if not value.startswith("@loader_path/"):
        raise ValueError("Staged native graph retains an external dependency.")
    candidate = origin.parent / value[len("@loader_path/"):]
    actual = candidate.resolve(strict=True)
    if not actual.is_relative_to(root.resolve()) or not actual.is_file() or actual.suffix != ".dylib" or candidate.is_symlink():
        raise ValueError("Staged native dependency escapes its sealed graph.")
    return actual


def stage_native_graph(executables: dict[str, Path], prefix: Path, destination: Path) -> dict:
    if prefix.is_symlink() or not prefix.is_dir() or destination.exists() or destination.is_symlink():
        raise ValueError("Native staging requires a clean prefix and new destination.")
    if not 1 <= len(executables) <= 20:
        raise ValueError("Native executable selection is invalid.")
    selected: dict[str, Path] = {}
    edges: dict[str, list[tuple[str, str]]] = {}
    pending = []
    for name, path in executables.items():
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", name) or path.is_symlink() or not path.is_file() or not os.access(path, os.X_OK):
            raise ValueError("Native executable selection contains an unsafe member.")
        selected[name] = path
        pending.append(name)
    while pending:
        name = pending.pop()
        origin = selected[name]
        references = []
        for reference in dependencies(origin):
            candidate = library_candidate(reference, origin=origin, prefix=prefix)
            if candidate is None:
                continue
            label = "lib/" + candidate.name
            # otool lists a dylib's own install name first; it is not an edge.
            if candidate.resolve() == origin.resolve():
                continue
            if label in selected and selected[label].resolve() != candidate.resolve():
                raise ValueError("Two native libraries collide under the same staged name.")
            if label not in selected:
                selected[label] = candidate
                pending.append(label)
            references.append((reference, label))
        edges[name] = references
        if len(selected) > 300:
            raise ValueError("Native dependency graph exceeds the build limit.")
    destination.mkdir(parents=True, mode=0o700)
    transformations = []
    for name, origin in sorted(selected.items()):
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        actual = origin.resolve(strict=True)
        if not clone_file(actual, target):
            shutil.copy2(actual, target)
        target.chmod(0o755 if "/" not in name else 0o644)
        arguments = []
        for reference, label in edges[name]:
            new = "@loader_path/" + (Path(label).name if name.startswith("lib/") else label)
            arguments.extend(["-change", reference, new])
        for value in dict.fromkeys(rpaths(target)):
            arguments.extend(["-delete_rpath", value])
        if name.startswith("lib/"):
            arguments.extend(["-id", "@loader_path/" + target.name])
        if arguments:
            subprocess.run(["/usr/bin/install_name_tool", *arguments, str(target)], check=True, capture_output=True, timeout=120)
        subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", str(target)], check=True, capture_output=True, timeout=120)
        for reference in dependencies(target):
            if not system_dependency(reference) and not reference.startswith("@loader_path/"):
                raise ValueError("Staged native graph retains an external dependency.")
        if rpaths(target):
            raise ValueError("Staged native graph retains a build-prefix search path.")
        transformations.append({"path": name, "input_sha256": digest_file(actual), "staged_sha256": digest_file(target), "size": target.stat().st_size})
    # Resolve after every graph member exists, including dylibs' own install IDs.
    for name in selected:
        target = destination / name
        for reference in dependencies(target):
            staged_dependency(reference, origin=target, root=destination)
        subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(target)], check=True, capture_output=True, timeout=120)
    result = {"schema_version": 1, "files": transformations, "executables": sorted(executables), "acceptance_tested": False, "license_review_complete": False}
    atomic_write_json(destination / "native-staging.json", result)
    return result
