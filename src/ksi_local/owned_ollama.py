"""Authorize model requests against the actual owned local TCP connection."""

import contextlib
import http.client
import subprocess
import time
import urllib.request
from contextvars import ContextVar
from urllib.parse import urlsplit

from ksi_local.network_policy import LocalModelRedirect, NetworkPolicyError, require_loopback_http_url


_SERVICE: ContextVar[tuple[int, str] | None] = ContextVar("ksi_owned_ollama", default=None)


@contextlib.contextmanager
def owned_server_identity(pid: int, base_url: str):
    if type(pid) is not int or not 1 <= pid < 2**31:
        raise ValueError("Owned model service identity is invalid.")
    token = _SERVICE.set((pid, require_loopback_http_url(base_url).rstrip("/")))
    try:
        yield
    finally:
        _SERVICE.reset(token)


def _sockets(pid: int, port: int, state: str) -> list[str]:
    try:
        result = subprocess.run(["/usr/sbin/lsof", "-nP", "-a", "-p", str(pid),
            "-iTCP:" + str(port), "-sTCP:" + state, "-F", "pn"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0 or len(result.stdout) > 65536:
        return []
    lines = result.stdout.splitlines()
    return lines if "p" + str(pid) in lines else []


def owns_listener(pid: int, base_url: str) -> bool:
    parsed = urlsplit(require_loopback_http_url(base_url))
    return bool(_sockets(pid, parsed.port or 80, "LISTEN"))


def _address(endpoint) -> str:
    host, port = endpoint[:2]
    return ("[" + host + "]" if ":" in host else host) + ":" + str(port)


def verify_connection_owner(pid: int, connection) -> None:
    # Connect first, check the server's exact reverse four-tuple, THEN allow
    # HTTP headers/body. A foreign listener cannot race an earlier readiness
    # probe into receiving text. A replacement cannot take an established TCP
    # connection belonging to the previous server.
    local, peer = connection.getsockname(), connection.getpeername()
    expected = "n" + _address(peer) + "->" + _address(local)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if expected in _sockets(pid, peer[1], "ESTABLISHED"):
            return
        time.sleep(0.02)  # Give the owned server time to accept the connection.
    raise NetworkPolicyError("Model bağlantısı KSI tarafından başlatılan sunucuya ait değil; metin gönderilmedi.")


def open_owned_loopback(request, *, timeout):
    identity = _SERVICE.get()
    parsed = urlsplit(request.full_url)
    base_url = require_loopback_http_url("http://" + parsed.netloc).rstrip("/")
    if parsed.scheme != "http" or parsed.query or parsed.fragment or identity is None or identity[1] != base_url:
        raise NetworkPolicyError("Paketlenmiş model isteği etkin KSI sunucu oturumuna bağlı olmalıdır.")
    pid = identity[0]

    class OwnedConnection(http.client.HTTPConnection):
        def connect(self):
            super().connect()
            try:
                verify_connection_owner(pid, self.sock)
            except BaseException:
                self.close()
                raise

    class OwnedHandler(urllib.request.HTTPHandler):
        def http_open(self, req):
            return self.do_open(OwnedConnection, req)

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), LocalModelRedirect(), OwnedHandler())
    return opener.open(request, timeout=timeout)
