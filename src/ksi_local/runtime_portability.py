"""Audit an installed runtime before it is labelled as an offline package."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from ksi_local.atomic_files import atomic_write_text


_PYTHON_VERSION = "3.12.13"
_PYTHON_BINARY = "python3.12"
_VIRTUAL_ENVIRONMENTS = ("venv", "chatterbox-venv")
_MACHINE_PATH_PREFIXES = (
    "/Users/",
    "/Volumes/",
    "/opt/homebrew/",
    "/usr/local/",
    "/private/tmp/",
    "/tmp/",
)
_UNUSED_EXTERNAL_QT_SQL_DRIVERS = (
    "libqsqlmimer.dylib",
    "libqsqlodbc.dylib",
    "libqsqlpsql.dylib",
)


@dataclass(frozen=True)
class PortabilityFinding:
    relative_path: str
    rule: str
    detail: str


def _is_macho(path: Path) -> bool:
    if path.suffix in {".so", ".dylib"} or path.name.startswith("python"):
        try:
            return path.read_bytes()[:4] in {
                b"\xca\xfe\xba\xbe",
                b"\xcf\xfa\xed\xfe",
                b"\xfe\xed\xfa\xcf",
            }
        except OSError:
            return False
    return False


def _unlink_tree_entry(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def prepare_runtime_layout(root: str | Path) -> None:
    """Relink copied virtual environments to the bundled relocatable Python.

    This function is intentionally limited to a staged runtime tree. It removes
    development-only console scripts and bytecode so local build paths cannot be
    carried into the offline package.
    """
    unresolved = Path(root).expanduser()
    if unresolved.is_symlink():
        raise ValueError("Runtime kökü sembolik bağlantı olamaz.")
    runtime = unresolved.resolve()
    if not runtime.is_dir():
        raise ValueError("Runtime kökü bulunamadı.")
    python_root = runtime / "python"
    python_binary = python_root / "bin" / _PYTHON_BINARY
    if python_root.is_symlink() or python_binary.is_symlink() or not python_binary.is_file():
        raise ValueError("Taşınabilir Python runtime içinde bulunamadı.")
    if not python_binary.stat().st_mode & 0o111:
        raise ValueError("Taşınabilir Python çalıştırılabilir değil.")

    for environment_name in _VIRTUAL_ENVIRONMENTS:
        environment = runtime / environment_name
        bin_directory = environment / "bin"
        if environment.is_symlink() or bin_directory.is_symlink() or not bin_directory.is_dir():
            raise ValueError(f"{environment_name} geçerli bir sanal ortam değildir.")
        for child in tuple(bin_directory.iterdir()):
            _unlink_tree_entry(child)
        (bin_directory / _PYTHON_BINARY).symlink_to(
            Path("../../python/bin") / _PYTHON_BINARY
        )
        (bin_directory / "python3").symlink_to(_PYTHON_BINARY)
        (bin_directory / "python").symlink_to(_PYTHON_BINARY)
        atomic_write_text(
            environment / "pyvenv.cfg",
            "include-system-site-packages = false\n"
            f"version = {_PYTHON_VERSION}\n",
            mode=0o644,
        )

    sql_drivers = (
        runtime
        / "venv/lib/python3.12/site-packages/PySide6/Qt/plugins/sqldrivers"
    )
    for filename in _UNUSED_EXTERNAL_QT_SQL_DRIVERS:
        driver = sql_drivers / filename
        if driver.is_symlink() or driver.is_file():
            driver.unlink()

    for cache in sorted(runtime.rglob("__pycache__"), reverse=True):
        _unlink_tree_entry(cache)
    for suffix in ("*.pyc", "*.pyo"):
        for bytecode in runtime.rglob(suffix):
            _unlink_tree_entry(bytecode)


def audit_runtime_portability(root: str | Path) -> tuple[PortabilityFinding, ...]:
    unresolved = Path(root).expanduser()
    if unresolved.is_symlink():
        raise ValueError("Runtime kökü sembolik bağlantı olamaz.")
    runtime = unresolved.resolve()
    if not runtime.is_dir():
        raise ValueError("Runtime kökü bulunamadı.")
    findings: list[PortabilityFinding] = []
    for path in sorted(runtime.rglob("*")):
        relative = path.relative_to(runtime).as_posix()
        if path.is_symlink():
            try:
                resolved = path.resolve(strict=True)
            except OSError:
                findings.append(PortabilityFinding(relative, "broken-symlink", "hedef yok"))
                continue
            if not resolved.is_relative_to(runtime):
                findings.append(
                    PortabilityFinding(relative, "external-symlink", "runtime dışı hedef")
                )
            continue
        if path.name == "pyvenv.cfg" and path.is_file():
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                text = ""
            if "/opt/homebrew/" in text or "/usr/local/" in text:
                findings.append(
                    PortabilityFinding(relative, "external-python-home", "makineye bağlı Python")
                )
            elif any(prefix in text for prefix in _MACHINE_PATH_PREFIXES):
                findings.append(
                    PortabilityFinding(
                        relative,
                        "machine-local-python-path",
                        "makineye bağlı Python yolu",
                    )
                )
        if not path.is_file() or not _is_macho(path):
            continue
        try:
            completed = subprocess.run(
                ("/usr/bin/otool", "-L", str(path)),
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            findings.append(PortabilityFinding(relative, "otool-failed", "Mach-O okunamadı"))
            continue
        for line in completed.stdout.splitlines()[1:]:
            if not line[:1].isspace():
                continue
            dependency = line.strip().split(" (", 1)[0]
            if Path(dependency).name == path.name:
                continue
            if dependency.startswith(("/System/", "/usr/lib/")):
                continue
            if dependency.startswith("/"):
                findings.append(
                    PortabilityFinding(relative, "external-library", dependency)
                )
    return tuple(dict.fromkeys(findings))


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) == 2 and arguments[0] == "prepare":
        prepare_runtime_layout(arguments[1])
        print(json.dumps({"prepared": True}, ensure_ascii=False))
        return 0
    if len(arguments) != 1:
        print(
            "Kullanım: python -m ksi_local.runtime_portability [prepare] <runtime>",
            file=sys.stderr,
        )
        return 2
    findings = audit_runtime_portability(arguments[0])
    print(
        json.dumps(
            {"portable": not findings, "findings": [asdict(item) for item in findings]},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if not findings else 4


if __name__ == "__main__":
    raise SystemExit(main())
