"""Bounded cancellable native processes; no shell and no GUI dependencies."""

from __future__ import annotations

import os
import selectors
import signal
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable

from ksi_local.network_policy import local_worker_environment
from ksi_local.privacy import redact_sensitive_text
from ksi_local.resource_governor import active_model_descriptor
from ksi_local.job_leases import active_job_descriptor
from ksi_local.bundle_runtime import OfflinePayload, bundle_root
from ksi_local.workspace_access import active_workspace_descriptor


class OperationCancelled(RuntimeError):
    pass


def _stop_owned(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=3)


def run_engine(
    argv: list[str], *, cwd: Path | None = None, timeout: float = 3600,
    cancel: threading.Event | None = None,
    on_line: Callable[[str], None] | None = None,
    environment: dict[str, str] | None = None,
) -> str:
    if not argv or not all(isinstance(item, str) and "\x00" not in item for item in argv):
        raise ValueError("Motor komutu geçersiz.")
    if not 0 < timeout <= 24 * 3600:
        raise ValueError("Motor süre sınırı geçersiz.")
    if cancel is not None and cancel.is_set():
        raise OperationCancelled("İşlem iptal edildi.")
    descriptor = active_model_descriptor()
    workspace_descriptor = active_workspace_descriptor()
    descriptors = tuple(dict.fromkeys(fd for fd in (descriptor, active_job_descriptor(), workspace_descriptor) if fd is not None))
    worker_environment = local_worker_environment(environment)
    resources = bundle_root()
    if resources is not None and Path(argv[0]).name in {"ffmpeg", "magick"}:
        payload = OfflinePayload.load(resources)
        font_config = payload.component("support", "fontconfig-config")
        font = payload.component("support", "subtitle-font")
        if font.parent != font_config.parent:
            raise ValueError("Portable font configuration does not match the sealed font directory.")
        worker_environment["FONTCONFIG_PATH"] = str(font_config.parent)
        worker_environment["FONTCONFIG_FILE"] = str(font_config)
        worker_environment.pop("FONTCONFIG_SYSROOT", None)
    worker_environment.pop("KSI_MODEL_LOCK_FD", None)
    worker_environment.pop("KSI_WORKSPACE_LOCK_FD", None)
    if workspace_descriptor is not None:
        worker_environment["KSI_WORKSPACE_LOCK_FD"] = str(workspace_descriptor)
    if descriptor is not None:
        worker_environment["KSI_MODEL_LOCK_FD"] = str(descriptor)
    process = subprocess.Popen(
        argv, cwd=cwd, env=worker_environment,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        start_new_session=True, shell=False,
        pass_fds=descriptors,
    )
    lines: deque[str] = deque(maxlen=80)
    buffer = b""
    started = time.monotonic()
    try:
        assert process.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                if cancel is not None and cancel.is_set():
                    raise OperationCancelled("İşlem iptal edildi.")
                if time.monotonic() - started > timeout:
                    raise TimeoutError("Yerel motor süre sınırını aştı.")
                for key, _ in selector.select(timeout=0.1):
                    chunk = os.read(key.fileobj.fileno(), 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        break
                    buffer += chunk
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        text = line.decode("utf-8", errors="replace")[:4096]
                        lines.append(redact_sensitive_text(text))
                        if on_line:
                            on_line(text)
                    if len(buffer) > 16384:
                        # A hostile/broken tool must not grow an unbounded line.
                        buffer = buffer[-8192:]
            remaining = max(0.1, timeout - (time.monotonic() - started))
            process.wait(timeout=remaining)
        if buffer:
            lines.append(redact_sensitive_text(buffer.decode("utf-8", errors="replace")[:4096]))
        if process.returncode != 0:
            detail = "\n".join(lines)[-2000:]
            raise RuntimeError(f"Yerel motor işlemi başarısız (kod {process.returncode}). {detail}")
        return "\n".join(lines)
    finally:
        _stop_owned(process)
        if process.stdout is not None:
            process.stdout.close()
