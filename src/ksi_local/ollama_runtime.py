"""Start Ollama only for a processing operation, then stop it."""

from __future__ import annotations

import contextlib
import os
import subprocess
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

    models = Path(models_directory).expanduser().resolve()
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
    process = subprocess.Popen(
        [sys.executable, "-B", "-m", "ksi_local.owned_service",
         str(Path(executable).expanduser().resolve()), "serve"],
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        pass_fds=descriptors,
    )
    deadline = time.monotonic() + startup_timeout_seconds
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("Ollama sunucusu başlatılamadı.")
            if _is_ready(base_url):
                yield True
                return
            time.sleep(0.2)
        raise RuntimeError("Ollama sunucusu zamanında hazır olmadı.")
    finally:
        if process.stdin is not None:
            process.stdin.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
