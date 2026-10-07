"""Start Ollama only for a processing operation, then stop it."""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import selectors
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from urllib.error import URLError

from ksi_local.network_policy import NetworkPolicyError, local_worker_environment, require_loopback_http_url
from ksi_local.network_policy import open_loopback as urlopen
from ksi_local.bundle_runtime import bundle_root
from ksi_local.resource_governor import active_model_descriptor
from ksi_local.workspace_access import active_workspace_descriptor
from ksi_local.job_leases import active_job_descriptor
from ksi_local.owned_ollama import owned_server_identity, owns_listener


def _read_owned_pid(process, deadline: float) -> int:
    if process.stdout is None:
        raise RuntimeError("Yerel model sunucusu kimlik kanalı bulunamadı.")
    data = b""
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            for key, _ in selector.select(timeout=0.1):
                block = os.read(key.fileobj.fileno(), 128)
                if not block:
                    raise RuntimeError("Yerel model sunucusu kimlik kanalı kapandı.")
                data += block
                if len(data) > 128:
                    raise RuntimeError("Yerel model sunucusu kimlik yanıtı geçersiz.")
                if b"\n" in data:
                    try:
                        record = json.loads(data)
                        pid = record["pid"]
                    except (ValueError, KeyError, TypeError) as error:
                        raise RuntimeError("Yerel model sunucusu kimlik yanıtı geçersiz.") from error
                    if type(pid) is not int or not 1 <= pid < 2**31:
                        raise RuntimeError("Yerel model sunucusu kimliği geçersiz.")
                    return pid
    raise RuntimeError("Yerel model sunucusu kimliği zamanında alınamadı.")


def _is_ready(base_url: str, *, timeout: float = 0.5) -> bool:
    base_url = require_loopback_http_url(base_url)
    try:
        with urlopen(f"{base_url.rstrip('/')}/api/tags", timeout=timeout) as response:
            return response.status == 200
    except (OSError, URLError, TimeoutError, NetworkPolicyError):
        return False


@contextlib.contextmanager
def managed_ollama(
    *,
    executable: str,
    models_directory: str | Path,
    base_url: str = "http://127.0.0.1:11435",
    startup_timeout_seconds: float = 30,
) -> Iterator[bool]:
    """Yield whether this context started the server; always stop owned servers."""
    base_url = require_loopback_http_url(base_url)
    if _is_ready(base_url):
        if bundle_root() is not None:
            raise RuntimeError("KSI model portu başka bir sunucu tarafından kullanılıyor; yabancı model deposu kullanılmadı.")
        yield False
        return

    raw_models = Path(models_directory).expanduser()
    resources = bundle_root()
    if resources is not None:
        from ksi_local.installed_model_integrity import verify_ollama_store
        verify_ollama_store(raw_models, resources)
    models = raw_models.resolve()
    if not models.is_dir():
        raise ValueError("Ollama model klasörü bulunamadı.")
    host = base_url.removeprefix("http://").removeprefix("https://")
    environment = local_worker_environment(os.environ)
    environment.update(
        {
            "OLLAMA_HOST": host,
            "OLLAMA_MODELS": str(models),
            "OLLAMA_MAX_LOADED_MODELS": "1",
            "OLLAMA_NUM_PARALLEL": "1",
            "OLLAMA_NO_CLOUD": "true",
        }
    )
    descriptors = tuple(dict.fromkeys(fd for fd in (
        active_model_descriptor(), active_workspace_descriptor(), active_job_descriptor()
    ) if fd is not None))
    command = [sys.executable, "-B", "-m", "ksi_local.owned_service",
         str(Path(executable).expanduser().resolve()), "serve"]
    if resources is not None:
        command.append("--report-pid")
    process = subprocess.Popen(
        command,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE if resources is not None else subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        pass_fds=descriptors,
    )
    deadline = time.monotonic() + startup_timeout_seconds
    try:
        pid = _read_owned_pid(process, deadline) if resources is not None else None
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("Ollama sunucusu başlatılamadı.")
            if _is_ready(base_url):
                if pid is not None:
                    if not owns_listener(pid, base_url):
                        raise RuntimeError("Model portu KSI sunucusuna ait değil; yabancı sunucu kullanılmadı.")
                    with owned_server_identity(pid, base_url):
                        yield True
                else:
                    yield True
                return
            time.sleep(0.2)
        raise RuntimeError("Ollama sunucusu zamanında hazır olmadı.")
    finally:
        if process.stdout is not None:
            process.stdout.close()
        if process.stdin is not None:
            process.stdin.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
