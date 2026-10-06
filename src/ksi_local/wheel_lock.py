"""Build-only wheel locks: public metadata in, hash-verified wheelhouse out.

Pip reports contain local environment information and are never published.
Only the narrowly selected artifact metadata enters a distributable lock.
"""

from __future__ import annotations

import json
import re
import urllib.request
import urllib.error
from pathlib import Path
from urllib.parse import unquote, urlsplit

from ksi_local.build_inputs import SecureRedirect, fetch_pinned_input


def validate_wheel_lock(data: dict) -> tuple[dict, ...]:
    if not isinstance(data, dict) or data.get("schema_version") != 1 or data.get("architecture") not in {"arm64", "x86_64"} or data.get("python") != "3.12":
        raise ValueError("Wheel lock platform is invalid.")
    rows = data.get("wheels")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 500:
        raise ValueError("Wheel lock has an invalid package count.")
    names, filenames = set(), set()
    from packaging.tags import compatible_tags, cpython_tags, mac_platforms
    from packaging.utils import canonicalize_name, parse_wheel_filename

    platforms = list(mac_platforms((14, 0), data["architecture"]))
    supported = set(cpython_tags((3, 12), platforms=platforms)) | set(compatible_tags((3, 12), interpreter="cp312", platforms=platforms))
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Wheel entry is invalid.")
        name, version, filename = (row.get(key) for key in ("name", "version", "filename"))
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name):
            raise ValueError("Wheel name is invalid.")
        if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+!-]{0,127}", version):
            raise ValueError("Wheel version is invalid.")
        if not isinstance(filename, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+!-]+\.whl", filename):
            raise ValueError("Wheel filename is invalid.")
        wheel_name, wheel_version, _, tags = parse_wheel_filename(filename)
        if wheel_name != canonicalize_name(name) or str(wheel_version) != version or not supported.intersection(tags):
            raise ValueError("Wheel does not match its package or supported macOS architecture.")
        url = row.get("url")
        parsed = urlsplit(url) if isinstance(url, str) else None
        if parsed is None or parsed.scheme != "https" or parsed.hostname != "files.pythonhosted.org" or parsed.username or parsed.password or parsed.query or parsed.fragment or unquote(parsed.path.rsplit("/", 1)[-1]) != filename:
            raise ValueError("Wheel must come from its exact public PyPI artifact.")
        if not isinstance(row.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", row["sha256"]) or type(row.get("size")) is not int or not 0 < row["size"] <= 2 * 1024**3:
            raise ValueError("Wheel hash or size is invalid.")
        canonical = re.sub(r"[-_.]+", "-", name).lower()
        if canonical in names or filename.casefold() in filenames:
            raise ValueError("Wheel lock contains a duplicate package.")
        names.add(canonical)
        filenames.add(filename.casefold())
    return tuple(rows)


def lock_from_reports(reports: list[Path], *, architecture: str) -> dict:
    """Cross-check pip's artifact digests with bounded official PyPI metadata."""
    rows = []
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), SecureRedirect())
    for report in reports:
        if report.is_symlink() or report.stat().st_size > 16 * 1024**2:
            raise ValueError("Pip report exceeds the build metadata limit.")
        data = json.loads(report.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != "1" or not isinstance(data.get("install"), list):
            raise ValueError("Pip report is invalid.")
        for item in data["install"]:
            name, version = item["metadata"]["name"], item["metadata"]["version"]
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or not re.fullmatch(r"[A-Za-z0-9_.+!-]+", version):
                raise ValueError("Pip report package identity is invalid.")
            url = item["download_info"]["url"]
            expected = item["download_info"]["archive_info"]["hashes"]["sha256"]
            request = urllib.request.Request(f"https://pypi.org/pypi/{name}/{version}/json", headers={"User-Agent": "KSI-clean-build/1"})
            with opener.open(request, timeout=45) as response:
                content = response.read(4 * 1024**2 + 1)
            if len(content) > 4 * 1024**2:
                raise ValueError("PyPI metadata exceeds the build limit.")
            metadata = json.loads(content)
            artifact = next((entry for entry in metadata["urls"] if entry["url"] == url and entry["digests"]["sha256"] == expected and not entry.get("yanked")), None)
            if artifact is None or artifact.get("packagetype") != "bdist_wheel":
                raise ValueError(f"Official wheel digest does not match: {name}")
            rows.append({"name": name, "version": version, "filename": artifact["filename"], "url": url, "sha256": expected, "size": artifact["size"], "license": metadata["info"].get("license_expression") or "NOASSERTION"})
    result = {"schema_version": 1, "architecture": architecture, "python": "3.12", "minimum_macos": "14.0", "wheels": sorted(rows, key=lambda row: row["name"].casefold())}
    validate_wheel_lock(result)
    return result


def fetch_wheelhouse(data: dict, directory: Path, *, on_progress=None) -> list[Path]:
    rows = validate_wheel_lock(data)
    if directory.is_symlink():
        raise ValueError("Wheelhouse cannot be a symlink.")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    paths = []
    for index, row in enumerate(rows):
        for attempt in range(3):
            try:
                paths.append(fetch_pinned_input(row, (directory / row["filename"]).absolute()))
                break
            except (TimeoutError, ConnectionError, urllib.error.URLError):
                if attempt == 2:
                    raise
        if on_progress:
            on_progress(index + 1, len(rows))
    return paths


def requirements_text(data: dict) -> str:
    return "".join(f'{row["name"]}=={row["version"]} --hash=sha256:{row["sha256"]}\n' for row in validate_wheel_lock(data))
