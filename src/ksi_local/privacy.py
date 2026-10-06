"""Redact credentials and unnecessary URL query data before persistence or display."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit


_URL_PATTERN = re.compile(r"https://[^\s<>\"']+", re.IGNORECASE)
_SECRET_PATTERN = re.compile(
    r"(?i)\b(authorization|proxy-authorization|cookie|set-cookie|password|passwd|"
    r"access[_-]?token|refresh[_-]?token|api[_-]?key|auth_token|guest_token|ct0|"
    r"x-csrf-token)\b(\s*[:=]\s*)([^\s,;]+)"
)
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_BROWSER_SESSION_PATTERN = re.compile(
    r"(?i)(--cookies-from-browser\s+)(?:chrome|firefox):[^\s\r\n]+"
)
_USER_PATH_PATTERN = re.compile(r"/" + r"Users/[^/\s]+(?:/[^\r\n,;:'\"<>]+)?")
_VOLUME_PATH_PATTERN = re.compile(r"/" + r"Volumes/[^\r\n,;:'\"<>]+")


def _redact_url(match: re.Match[str]) -> str:
    value = match.group(0)
    trailing = ""
    while value and value[-1] in ".,);]":
        trailing = value[-1] + trailing
        value = value[:-1]
    try:
        parts = urlsplit(value)
    except ValueError:
        return "[URL gizlendi]" + trailing
    if not parts.query and not parts.fragment:
        return value + trailing
    redacted = urlunsplit((parts.scheme, parts.netloc, parts.path, "[gizlendi]", ""))
    return redacted + trailing


def redact_sensitive_text(text: str) -> str:
    """Remove common secrets and URL query strings from logs and error messages."""
    clean = _BEARER_PATTERN.sub("Bearer [gizlendi]", str(text))
    clean = _BROWSER_SESSION_PATTERN.sub(r"\1[ayrı-tarayıcı-oturumu]", clean)
    clean = _SECRET_PATTERN.sub(lambda match: f"{match.group(1)}{match.group(2)}[gizlendi]", clean)
    clean = _URL_PATTERN.sub(_redact_url, clean)
    clean = _USER_PATH_PATTERN.sub("[kullanıcı-dosya-yolu]", clean)
    return _VOLUME_PATH_PATTERN.sub("[harici-disk-yolu]", clean)


def safe_source_reference(source: str) -> tuple[str, str]:
    """Return a restartable source reference without arbitrary URL query data."""
    raw = source.strip()
    if not raw.lower().startswith("https://"):
        return "file", str(Path(raw).expanduser().resolve())
    parts = urlsplit(raw)
    host = (parts.hostname or "").lower()
    path = parts.path or "/"
    query = ""
    source_kind = "url"
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}:
        source_kind = "youtube"
        video_id = parse_qs(parts.query).get("v", [""])[0]
        if path == "/watch" and video_id:
            query = urlencode({"v": video_id})
    elif host in {"youtu.be", "www.youtu.be"}:
        source_kind = "youtube"
    elif host in {
        "x.com",
        "www.x.com",
        "mobile.x.com",
        "twitter.com",
        "www.twitter.com",
        "mobile.twitter.com",
    }:
        source_kind = "x"
    elif host == "udemy.com" or host.endswith(".udemy.com"):
        source_kind = "udemy"
    safe = urlunsplit(("https", parts.netloc.split("@")[-1], path, query, ""))
    return source_kind, safe
