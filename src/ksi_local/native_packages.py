"""Pinned public codec-library inputs used only by the clean native builder."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

from ksi_local.atomic_files import atomic_write_json, atomic_write_text
from ksi_local.build_inputs import fetch_pinned_input
from ksi_local.bundle_runtime import digest_file, host_architecture


def validate_native_lock(data: dict) -> list[dict]:
    if not isinstance(data, dict) or data.get("schema_version") != 1 or data.get("architecture") not in {"arm64", "x86_64"}:
        raise ValueError("Native library lock architecture is invalid.")
    rows = data.get("packages")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 300:
        raise ValueError("Native library count is invalid.")
    names = set()
    subdir = "osx-arm64" if data["architecture"] == "arm64" else "osx-64"
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str) or not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,127}", row["name"]) or row["name"] in names:
            raise ValueError("Native library identity is invalid or duplicated.")
        names.add(row["name"])
        filename = row.get("filename")
        if not isinstance(filename, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.!+-]+\.(?:conda|tar\.bz2)", filename):
            raise ValueError("Native library filename is invalid.")
        parsed = urlsplit(row.get("url", ""))
        if parsed.scheme != "https" or parsed.hostname != "conda.anaconda.org" or parsed.username or parsed.password or parsed.query or parsed.fragment or unquote(parsed.path) not in {f"/conda-forge/{subdir}/{filename}", f"/conda-forge/noarch/{filename}"}:
            raise ValueError("Native library must come from the exact public target artifact.")
        if not isinstance(row.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", row["sha256"]) or not isinstance(row.get("md5"), str) or not re.fullmatch(r"[0-9a-f]{32}", row["md5"]) or type(row.get("size")) is not int or not 0 < row["size"] <= 2 * 1024**3:
            raise ValueError("Native artifact integrity metadata is invalid.")
        if not isinstance(row.get("license"), str) or not row["license"] or "AGPL" in row["license"]:
            raise ValueError("Unexpected native redistribution scope; review the dependency selection.")
    return rows


def native_lock_from_report(report: Path, architecture: str) -> dict:
    if report.is_symlink() or report.stat().st_size > 16 * 1024**2:
        raise ValueError("Native solver report is unsafe.")
    data = json.loads(report.read_text(encoding="utf-8"))
    if not data.get("success"):
        raise ValueError("Cannot lock an unsuccessful native solve.")
    packages = [{"name": row["name"], "version": row["version"], "build": row["build_string"], "filename": row["fn"], "url": row["url"], "sha256": row["sha256"], "md5": row["md5"], "size": row["size"], "license": row["license"], "depends": row.get("depends", [])} for row in data["actions"]["LINK"]]
    result = {"schema_version": 1, "architecture": architecture, "minimum_macos": "14.0", "packages": sorted(packages, key=lambda row: row["name"]), "redistribution_review_complete": False}
    validate_native_lock(result)
    return result


def fetch_native_libraries(data: dict, cache: Path) -> list[Path]:
    paths = []
    for index, row in enumerate(validate_native_lock(data)):
        paths.append(fetch_pinned_input(row, (cache / row["filename"]).absolute()))
        print(f"Verified native input: {index + 1}/{len(data['packages'])}", flush=True)
    return paths


def install_native_prefix(data: dict, cache: Path, executable: Path,
                          executable_sha256: str, destination: Path) -> None:
    rows = validate_native_lock(data)
    if data["architecture"] != host_architecture():
        raise ValueError("Native prefix installation requires the actual target processor.")
    if executable.is_symlink() or not executable.is_file() or digest_file(executable) != executable_sha256:
        raise ValueError("Native installer is not the pinned build-only executable.")
    if not destination.is_absolute() or destination.exists() or destination.is_symlink():
        raise FileExistsError("Native prefix destination must be new and absolute.")
    files = []
    for row in rows:
        path = cache / row["filename"]
        if path.is_symlink() or not path.is_file() or path.stat().st_size != row["size"] or digest_file(path) != row["sha256"]:
            raise ValueError("Native package cache integrity failed.")
        # The solver's legacy MD5 fragment is only an installer format. Security
        # depends on the independent SHA-256 check immediately above.
        files.append(path.absolute().as_uri() + "#" + row["md5"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".ksi-native-install-", dir=destination.parent) as temporary:
        private = Path(temporary)
        explicit = private / "explicit.txt"
        atomic_write_text(explicit, "@EXPLICIT\n" + "\n".join(files) + "\n")
        environment = {"HOME": temporary, "PATH": "/usr/bin:/bin", "MAMBA_ROOT_PREFIX": str(private / "root"), "CONDA_OVERRIDE_OSX": "14.0"}
        subprocess.run([str(executable.absolute()), "--no-rc", "create", "--offline", "--yes", "--prefix", str(destination), "--file", str(explicit)], env=environment, check=True, timeout=900)
    sign_native_prefix(destination)


def sign_native_prefix(prefix: Path) -> dict:
    """Conda prefix rewriting invalidates Mach-O ad-hoc signatures on ARM."""
    if prefix.is_symlink() or not prefix.is_dir() or not (prefix / "conda-meta").is_dir():
        raise ValueError("Only an explicitly staged native package prefix may be signed.")
    records = []
    for path in sorted(prefix.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        with path.open("rb") as stream:
            magic = stream.read(4)
        if magic not in {b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"}:
            continue
        before = digest_file(path)
        subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", str(path)], check=True, capture_output=True, timeout=120)
        records.append({"path": path.relative_to(prefix).as_posix(), "before_sha256": before, "signed_sha256": digest_file(path)})
    result = {"schema_version": 1, "signing": "adhoc-after-prefix-rewrite", "files": records, "acceptance_tested": False}
    atomic_write_json(prefix / "native-prefix-signing.json", result)
    return result
