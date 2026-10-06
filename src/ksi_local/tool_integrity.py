"""Verify pinned download tools before they handle an untrusted media URL."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ksi_local.media import sha256_file


@dataclass(frozen=True)
class VerifiedTool:
    name: str
    path: str
    expected_version: str
    actual_version: str
    digest_verified: bool | None


@dataclass(frozen=True)
class ToolIntegrityReport:
    manifest: str
    tools: tuple[VerifiedTool, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest": self.manifest,
            "tools": [asdict(item) for item in self.tools],
        }


def manifest_path() -> Path:
    override = os.environ.get("KSI_TOOL_MANIFEST")
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parents[2] / "config" / "tool-manifest.json"


def load_tool_manifest(path: str | Path | None = None) -> dict[str, Any]:
    target = Path(path).expanduser().resolve() if path else manifest_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("Doğrulanmış araç manifesti okunamadı.") from error
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise RuntimeError("Araç manifesti sürümü desteklenmiyor.")
    if not isinstance(payload.get("tools"), dict):
        raise RuntimeError("Araç manifestinde araç kayıtları bulunamadı.")
    required = {"yt-dlp", "deno", "ffmpeg", "ffprobe"}
    records = payload["tools"]
    if any(not isinstance(records.get(name), dict) for name in required):
        raise RuntimeError("Araç manifestinde zorunlu kayıtlar eksik.")
    return payload


def pinned_tool_version(name: str, path: str | Path | None = None) -> str:
    tools = load_tool_manifest(path)["tools"]
    record = tools.get(name)
    if not isinstance(record, dict) or not isinstance(record.get("version"), str):
        raise RuntimeError(f"{name} için sabitlenmiş sürüm bulunamadı.")
    return str(record["version"])


def _version_output(
    executable: Path, *args: str, timeout_seconds: int = 45
) -> str:
    try:
        completed = subprocess.run(
            [str(executable), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"Araç çalıştırılamadı: {executable.name}") from error
    if completed.returncode != 0:
        raise RuntimeError(f"Araç sürümü okunamadı: {executable.name}")
    return next(
        (
            line.strip()
            for line in (completed.stdout + completed.stderr).splitlines()
            if line.strip()
        ),
        "",
    )


def _contains_version(output: str, expected: str) -> bool:
    pattern = rf"(?<!\d){re.escape(expected)}(?!\d)"
    return re.search(pattern, output) is not None


def _verify(
    name: str,
    executable: str | Path,
    record: dict[str, Any],
    *,
    version_args: tuple[str, ...],
    digest_key: str | None = None,
) -> VerifiedTool:
    path = Path(executable).expanduser().resolve()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise RuntimeError(f"{name} yürütülebilir dosyası bulunamadı.")
    expected = str(record.get("version") or "")
    if not expected:
        raise RuntimeError(f"{name} manifest sürümü eksik.")
    actual = _version_output(path, *version_args)
    if not _contains_version(actual, expected):
        raise RuntimeError(f"{name} sürümü manifestle eşleşmiyor.")
    digest_verified: bool | None = None
    if digest_key:
        expected_digest = record.get(digest_key)
        if not isinstance(expected_digest, str) or len(expected_digest) != 64:
            raise RuntimeError(f"{name} manifest özeti geçersiz.")
        digest_verified = sha256_file(path) == expected_digest.casefold()
        if not digest_verified:
            raise RuntimeError(f"{name} SHA-256 özeti manifestle eşleşmiyor.")
    return VerifiedTool(name, str(path), expected, actual, digest_verified)


def verify_download_tools(
    *,
    yt_dlp_path: str | Path,
    deno_path: str | Path,
    ffmpeg_path: str | Path | None = None,
    ffprobe_path: str | Path | None = None,
    path: str | Path | None = None,
) -> ToolIntegrityReport:
    target_manifest = Path(path).expanduser().resolve() if path else manifest_path()
    records = load_tool_manifest(target_manifest)["tools"]
    ffmpeg = Path(ffmpeg_path or shutil.which("ffmpeg") or "")
    ffprobe = Path(ffprobe_path or shutil.which("ffprobe") or "")
    tools = (
        _verify(
            "yt-dlp",
            yt_dlp_path,
            records["yt-dlp"],
            version_args=("--version",),
            digest_key="sha256",
        ),
        _verify(
            "deno",
            deno_path,
            records["deno"],
            version_args=("--version",),
            digest_key="binary_sha256",
        ),
        _verify("ffmpeg", ffmpeg, records["ffmpeg"], version_args=("-version",)),
        _verify("ffprobe", ffprobe, records["ffprobe"], version_args=("-version",)),
    )
    return ToolIntegrityReport(str(target_manifest), tools)


def verify_local_probe_tool(
    ffprobe_path: str | Path | None = None, *, path: str | Path | None = None
) -> VerifiedTool:
    records = load_tool_manifest(path)["tools"]
    executable = Path(ffprobe_path or shutil.which("ffprobe") or "")
    return _verify("ffprobe", executable, records["ffprobe"], version_args=("-version",))
