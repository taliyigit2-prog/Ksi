"""Local-only network rules for AI and future document worker processes."""

from __future__ import annotations

import contextlib
import os
import socket
import urllib.request
from collections.abc import Iterator, Mapping
from typing import Any
from urllib.parse import urlsplit


LOCAL_POLICY_ENV = "KSI_NETWORK_POLICY"
LOCAL_POLICY_VALUE = "local-only"
_LOCAL_NAMES = frozenset({"localhost", "127.0.0.1", "::1"})
_CLOUD_SECRET_NAMES = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "AZURE_OPENAI_API_KEY",
        "COHERE_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GROQ_API_KEY",
        "HUGGING_FACE_HUB_TOKEN",
        "HF_TOKEN",
        "MISTRAL_API_KEY",
        "OLLAMA_API_KEY",
        "OPENAI_API_KEY",
        "TOGETHER_API_KEY",
    }
)
_PROXY_NAMES = frozenset(
    {
        "ALL_PROXY",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "all_proxy",
        "https_proxy",
        "http_proxy",
    }
)


class NetworkPolicyError(RuntimeError):
    """Raised when a local-only worker attempts remote network access."""


class LocalModelRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        raise NetworkPolicyError("Yerel model API'sinde yönlendirmeye izin verilmez.")


def open_loopback(request, *, timeout):
    """Do not send model text through an inherited proxy or HTTP redirect."""
    url = request.full_url if isinstance(request, urllib.request.Request) else request
    parsed = urlsplit(url)
    if parsed.scheme != "http" or not parsed.hostname or not is_loopback_host(parsed.hostname) or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise NetworkPolicyError("Model API isteği yalnız doğrulanmış yerel HTTP uç noktasına yapılabilir.")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), LocalModelRedirect())
    return opener.open(request, timeout=timeout)


def is_loopback_host(host: str) -> bool:
    """Return True only for KSI Local Studio's explicit local service host names."""
    normalized = host.strip().lower()
    return normalized in _LOCAL_NAMES


def require_loopback_http_url(value: str) -> str:
    """Validate and canonicalize an uncredentialed local HTTP service URL."""
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ValueError("Yerel model adresi geçerli değil.") from error
    if parsed.scheme.lower() != "http":
        raise ValueError("Yerel model adresi yalnız http kullanabilir.")
    if not parsed.hostname or not is_loopback_host(parsed.hostname):
        raise ValueError("Model adresi yalnız 127.0.0.1 veya localhost olabilir.")
    if parsed.username or parsed.password:
        raise ValueError("Yerel model adresinde kullanıcı bilgisi olamaz.")
    if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("Yerel model adresi yalnız kök adresi göstermelidir.")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("Yerel model portu geçerli değil.")
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    return f"http://{host}{f':{port}' if port is not None else ''}"


def local_worker_environment(
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build a worker environment that cannot silently opt into cloud AI."""
    environment = dict(os.environ if base is None else base)
    for name in _CLOUD_SECRET_NAMES | _PROXY_NAMES:
        environment.pop(name, None)
    environment.update(
        {
            LOCAL_POLICY_ENV: LOCAL_POLICY_VALUE,
            "NO_PROXY": "127.0.0.1,localhost,::1",
            "no_proxy": "127.0.0.1,localhost,::1",
            "OLLAMA_NO_CLOUD": "true",
            "HF_HUB_OFFLINE": "1",
            "HF_DATASETS_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
        }
    )
    return environment


def _require_local_socket_host(host: Any) -> None:
    if isinstance(host, bytes):
        host = host.decode("ascii", errors="ignore")
    if not isinstance(host, str) or not is_loopback_host(host):
        raise NetworkPolicyError(
            "Yerel belge işleyicisinin dış ağ bağlantısı engellendi."
        )


@contextlib.contextmanager
def local_only_socket_guard() -> Iterator[None]:
    """Temporarily block DNS and TCP connections except loopback.

    This guard changes process-global socket functions, so it is intended for the
    dedicated single-purpose document worker process, installed before document
    parsers or model clients are imported.
    """
    original_socket = socket.socket
    original_getaddrinfo = socket.getaddrinfo

    class LocalOnlySocket(original_socket):  # type: ignore[misc, valid-type]
        def connect(self, address: Any) -> None:
            if self.family in {socket.AF_INET, socket.AF_INET6}:
                _require_local_socket_host(address[0])
            super().connect(address)

        def connect_ex(self, address: Any) -> int:
            if self.family in {socket.AF_INET, socket.AF_INET6}:
                _require_local_socket_host(address[0])
            return super().connect_ex(address)

    def local_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        _require_local_socket_host(host)
        return original_getaddrinfo(host, *args, **kwargs)

    socket.socket = LocalOnlySocket
    socket.getaddrinfo = local_getaddrinfo
    try:
        yield
    finally:
        socket.socket = original_socket
        socket.getaddrinfo = original_getaddrinfo
