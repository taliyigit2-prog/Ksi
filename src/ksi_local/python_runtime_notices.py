"""Stage original standalone Python grants from exact build-only inputs.

This is notice restoration, not binary attestation or legal release approval.
No upstream code is executed and installed applications never download here.
"""

import hashlib
import json
import re
import subprocess
import tarfile
from pathlib import Path
from urllib.parse import urlsplit

from ksi_local.atomic_files import atomic_write_bytes, atomic_write_json
from ksi_local.bundle_runtime import digest_file, safe_member


def _metadata_references(value):
    result = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "license_path":
                if not isinstance(child, str):
                    raise ValueError("Original license path is malformed.")
                result.add(child)
            elif key == "license_paths":
                if not isinstance(child, list) or not all(isinstance(path, str) for path in child):
                    raise ValueError("Original license paths are malformed.")
                result.update(child)
            else:
                result.update(_metadata_references(child))
    elif isinstance(value, list):
        for child in value:
            result.update(_metadata_references(child))
    return result


def stage_python_runtime_notices(archive: Path, pin: dict, destination: Path, *, original_tar=None) -> dict:
    architecture = pin.get("architecture")
    triple = {"arm64": "aarch64-apple-darwin", "x86_64": "x86_64-apple-darwin"}.get(architecture)
    if triple is None or not destination.is_absolute() or destination.exists() or destination.is_symlink():
        raise ValueError("Original runtime notice stage needs a new explicit target architecture/root.")
    full = pin.get("full_archive", {})
    public_url = urlsplit(full.get("url", ""))
    if public_url.scheme != "https" or not public_url.hostname or public_url.username or public_url.password or public_url.query or public_url.fragment:
        raise ValueError("Original full Python archive needs a clean public HTTPS URL.")
    if (archive.is_symlink() or not archive.is_file() or type(full.get("size")) is not int
            or not 0 < full["size"] <= 128 * 1024**2 or archive.stat().st_size != full["size"]
            or not re.fullmatch(r"[0-9a-f]{64}", str(full.get("sha256", "")))
            or digest_file(archive) != full["sha256"]):
        raise ValueError("Original full Python archive differs from its reviewed pin.")
    rows = pin.get("notices")
    if not isinstance(rows, list) or not 2 <= len(rows) <= 128:
        raise ValueError("Original Python notices inventory is invalid.")
    selected, names = [], set()
    for row in rows:
        name = row.get("original_archive_member", "")
        safe_member(destination, name)
        if (name in names or not (name == "python/PYTHON.json" or name.startswith("python/licenses/")
                or name == "python/install/lib/python3.12/LICENSE.txt")
                or type(row.get("size")) is not int or not 0 < row["size"] <= 4 * 1024**2
                or not re.fullmatch(r"[0-9a-f]{64}", str(row.get("sha256", "")))):
            raise ValueError("Original Python notice member is unsafe, duplicated or unbounded.")
        names.add(name)
        # macOS libarchive reads the exact zstd archive without a Homebrew tool.
        # Each output has an independently pinned size and digest; nothing is
        # extracted or executed from the full distribution's build directories.
        if original_tar is None:
            data = subprocess.run(["/usr/bin/tar", "-xOf", str(archive), name],
                check=True, capture_output=True, timeout=30).stdout
        else:
            original_tar.seek(0)
            with tarfile.open(fileobj=original_tar, mode="r:") as stream:
                matches = [member for member in stream.getmembers() if member.name == name]
                if len(matches) != 1 or not matches[0].isfile() or matches[0].size != row["size"]:
                    raise ValueError("Original Python notice is missing, linked, duplicated or oversized.")
                with stream.extractfile(matches[0]) as content:
                    data = content.read(row["size"] + 1)
        if len(data) != row["size"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise ValueError("Original Python notice content differs from its reviewed member pin.")
        data.decode("utf-8")
        selected.append((row, data))
    metadata_values = [data for row, data in selected if row["original_archive_member"] == "python/PYTHON.json"]
    if len(metadata_values) != 1:
        raise ValueError("Original full Python distribution metadata is missing.")
    metadata = json.loads(metadata_values[0])
    if metadata.get("target_triple") != triple or metadata.get("python_version") != pin.get("python_version"):
        raise ValueError("Original Python metadata uses another version or processor.")
    available = {name.removeprefix("python/") for name in names}
    missing = sorted(_metadata_references(metadata) - available)
    if missing != pin.get("missing_original_license_paths") or missing not in ([], ["licenses/LICENSE.zlib-ng.txt"]):
        raise ValueError("Unreviewed missing original Python grants close notice restoration.")
    alternatives = []
    if missing:
        for name, variants in metadata.get("build_info", {}).get("extensions", {}).items():
            for variant in variants:
                if missing[0] in variant.get("license_paths", []):
                    links = variant.get("links", [])
                    if not any(link.get("name") == "z" and link.get("system") is True and "path_static" not in link for link in links):
                        raise ValueError("Missing zlib-ng grant cannot be excused for a shipped/static z provider.")
                    alternatives.append({"extension": name, "links": links})
        if {row["extension"] for row in alternatives} != {"_sqlite3", "_tkinter", "binascii", "zlib"}:
            raise ValueError("Missing upstream z provider reference has another unreviewed applicability.")
    if digest_file(archive) != full["sha256"]:
        raise ValueError("Original Python archive changed during notice restoration.")
    destination.mkdir(parents=True, mode=0o700)
    components = []
    for row, data in selected:
        relative = "licenses/python-standalone/" + row["original_archive_member"].removeprefix("python/")
        atomic_write_bytes(safe_member(destination, relative), data, mode=0o644)
        components.append({"path": relative, "size": row["size"], "sha256": row["sha256"],
            "identifier": "python-standalone-original-" + hashlib.sha256(relative.encode()).hexdigest()[:24],
            "role": "support" if relative.endswith("/PYTHON.json") else "license",
            "source_url": full["url"], "revision": full["sha256"]})
    binding = {"schema_version": 1, "architecture": architecture, "python_version": pin["python_version"],
        "original_full_archive": full, "original_install_only_archive": pin["install_only_archive"],
        "original_notice_members": rows, "missing_original_license_paths": missing,
        "original_system_z_provider_references": alternatives,
        "redistribution_review_complete": False, "final_binary_license_acceptance": False,
        "scope": "Exact original legal texts and metadata only. Missing upstream alternative-provider reference remains visible; actual signed binary/source binding is separate."}
    relative = "licenses/python-standalone/original-distribution-binding.json"
    target = safe_member(destination, relative)
    atomic_write_json(target, binding, mode=0o644)
    components.append({"path": relative, "size": target.stat().st_size, "sha256": digest_file(target),
        "identifier": "python-standalone-original-binding", "role": "support"})
    atomic_write_json(destination / "component-specification.json", {"schema_version": 1,
        "architecture": architecture, "files": sorted(components, key=lambda row: row["path"])}, mode=0o644)
    return {"architecture": architecture, "original_notices": len(selected), "files": len(components),
        "missing_original_license_paths": missing, "final_binary_license_acceptance": False}
