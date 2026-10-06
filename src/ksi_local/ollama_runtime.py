"""Start Ollama only for a processing operation, then stop it."""

from __future__ import annotations

import contextlib
import os
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from ksi_local.network_policy import local_worker_environment, require_loopback_http_url


def _is_ready(base_url: str, *, timeout: float = 0.5) -> bool:
    base_url = require_loopback_http_url(base_url)
    try:
        with urlopen(f"{base_url.rstrip('/')}/api/tags", timeout=timeout) as response:
            return response.status == 200
    except (OSError, URLError, TimeoutError):
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
    process = subprocess.Popen(
        [str(Path(executable).expanduser().resolve()), "serve"],
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
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
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
