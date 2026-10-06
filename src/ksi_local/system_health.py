"""Dependency and host readiness checks for Phase 1."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable
from ksi_local.bundle_runtime import bundle_root, tool_path


@dataclass(frozen=True)
class ToolStatus:
    name: str
    path: str | None
    version: str | None
    installed: bool
    running: bool | None = None
    note: str | None = None


@dataclass(frozen=True)
class HealthReport:
    architecture: str
    macos_version: str
    python_version: str
    tools: tuple[ToolStatus, ...]
    youtube_js_ready: bool
    youtube_js_runtime: str | None

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)


def _first_nonempty_line(value: str) -> str | None:
    return next((line.strip() for line in value.splitlines() if line.strip()), None)


def _command_output(argv: list[str], timeout: int = 5) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
    except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired):
        return 127, ""
    combined = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
    return completed.returncode, combined


def _tool(
    name: str,
    version_args: Iterable[str],
    *,
    configured_path: str | Path | None = None,
    timeout_seconds: int = 15,
) -> ToolStatus:
    if bundle_root() is not None:
        try:
            configured_path = tool_path(name)
        except (OSError, RuntimeError, ValueError) as error:
            return ToolStatus(name, None, None, False, note=str(error))
    path = (
        str(configured_path)
        if configured_path and Path(configured_path).is_file()
        else shutil.which(name)
    )
    if path is None:
        return ToolStatus(name, None, None, False)
    return_code, output = _command_output([path, *version_args], timeout=timeout_seconds)
    return ToolStatus(name, path, _first_nonempty_line(output), return_code == 0)


def _manifest_tool_path(name: str) -> Path | None:
    override = os.environ.get("KSI_TOOL_MANIFEST")
    target = (
        Path(override).expanduser()
        if override
        else Path(__file__).resolve().parents[2] / "config/tool-manifest.json"
    )
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        record = payload["tools"][name]
        configured = Path(str(record["path"])).expanduser()
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return configured if configured.is_file() else None


def _ollama_status() -> ToolStatus:
    base = _tool("ollama", ("--version",))
    if not base.installed or base.path is None:
        return base
    _, version_output = _command_output([base.path, "--version"])
    version_match = re.search(r"(?:client version is|ollama version is)\s+(\d+\.\d+\.\d+)", version_output)
    version = version_match.group(1) if version_match else base.version
    return_code, _ = _command_output([base.path, "ps"])
    running = return_code == 0
    note = "Sunucu çalışıyor." if running else "Kurulu; sunucu çalışmıyor."
    return ToolStatus(base.name, base.path, version, True, running, note)


def _vision_ocr_status() -> ToolStatus:
    if bundle_root() is not None:
        try:
            helper = Path(tool_path("ocr-helper"))
        except (OSError, RuntimeError, ValueError) as error:
            return ToolStatus("Apple Vision OCR", None, None, False, note=str(error))
    else:
        root = Path(__file__).resolve().parents[2]
        candidates = (root / "bin/KSIOCR", root / "build/KSIOCR")
        helper = next((item for item in candidates if item.is_file()), None)
    if helper is None or helper.is_symlink():
        return ToolStatus("Apple Vision OCR", None, None, False)
    return_code, output = _command_output([str(helper), "capabilities"], timeout=30)
    try:
        payload = json.loads(output)
        languages = payload["supported_languages"]
        revision = int(payload["revision"])
        if not isinstance(languages, list):
            raise TypeError
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return ToolStatus(
            "Apple Vision OCR",
            str(helper),
            None,
            False,
            note="Yerel OCR yetenekleri okunamadı.",
        )
    ready = return_code == 0 and bool(languages)
    return ToolStatus(
        "Apple Vision OCR",
        str(helper),
        f"Vision revizyon {revision}",
        ready,
        note=f"{len(languages)} dil varyantı; otomatik dil algılama açık.",
    )


def _version_tuple(version_line: str | None) -> tuple[int, int, int] | None:
    if not version_line:
        return None
    match = re.search(r"(?:^|\D)(\d+)\.(\d+)(?:\.(\d+))?", version_line)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)


def _major_version(version_line: str | None) -> int | None:
    version = _version_tuple(version_line)
    return version[0] if version else None


def build_health_report() -> HealthReport:
    workspace = None
    try:
        from ksi_local.settings import resolve_workspace

        workspace = resolve_workspace()
    except RuntimeError:
        pass
    ffmpeg = _tool(
        "ffmpeg", ("-version",), configured_path=_manifest_tool_path("ffmpeg")
    )
    ffprobe = _tool(
        "ffprobe", ("-version",), configured_path=_manifest_tool_path("ffprobe")
    )
    yt_dlp = _tool(
        "yt-dlp",
        ("--version",),
        configured_path=workspace.yt_dlp if workspace else None,
        # The signed PyInstaller binary can need more than 15 seconds for its
        # first launch from an exFAT SSD after reconnecting the drive.
        timeout_seconds=45,
    )
    deno = _tool(
        "deno",
        ("--version",),
        configured_path=workspace.deno if workspace else None,
    )
    node = _tool("node", ("--version",), configured_path=_manifest_tool_path("node"))
    ollama = _ollama_status()
    vision_ocr = _vision_ocr_status()

    js_runtime: str | None = None
    if deno.installed and (_version_tuple(deno.version) or (0, 0, 0)) >= (2, 3, 0):
        js_runtime = "deno"
    elif node.installed and (_version_tuple(node.version) or (0, 0, 0)) >= (22, 0, 0):
        js_runtime = "node"

    return HealthReport(
        architecture=platform.machine(),
        macos_version=platform.mac_ver()[0],
        python_version=sys.version.split()[0],
        tools=(ffmpeg, ffprobe, yt_dlp, deno, node, ollama, vision_ocr),
        youtube_js_ready=js_runtime is not None,
        youtube_js_runtime=js_runtime,
    )


def format_health_report(report: HealthReport) -> str:
    lines = [
        f"Mimari: {report.architecture}",
        f"macOS: {report.macos_version}",
        f"Python: {report.python_version}",
        "",
        "Araçlar:",
    ]
    for tool in report.tools:
        state = "hazır" if tool.installed else "eksik"
        details = f" — {tool.version}" if tool.version else ""
        note = f" ({tool.note})" if tool.note else ""
        lines.append(f"- {tool.name}: {state}{details}{note}")
    js_state = report.youtube_js_runtime or "uygun çalışma ortamı yok"
    lines.extend(("", f"YouTube JS hazırlığı: {js_state}"))
    return "\n".join(lines)


def executable_path(report: HealthReport, name: str) -> Path | None:
    for tool in report.tools:
        if tool.name == name and tool.path:
            return Path(tool.path)
    return None
