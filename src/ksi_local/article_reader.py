"""Bounded local article extraction; page text is always untrusted data."""

from __future__ import annotations

import ipaddress
import http.client
import os
import re
import shutil
import socket
import ssl
import tempfile
import urllib.robotparser
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

from ksi_local.atomic_files import atomic_write_json, atomic_write_text
from ksi_local.document_output import create_document_outputs

MAX_ARTICLE_BYTES = 8 * 1024 * 1024
MAX_ARTICLE_TEXT = 500_000


@dataclass(frozen=True)
class Article:
    source_url: str
    title: str
    author: str | None
    published_at: str | None
    accessed_at: str
    text: str
    images: tuple[str, ...]
    warnings: tuple[str, ...]


def _validated_article_target(
    raw_url: str, *, resolver=socket.getaddrinfo
) -> tuple[str, tuple[str, ...]]:
    parsed = urlsplit(raw_url.strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Makale için kimlik bilgisi içermeyen bir HTTPS adresi gerekir.")
    if parsed.port not in (None, 443):
        raise ValueError("Makale adresinde yalnız varsayılan HTTPS portu kullanılabilir.")
    host = parsed.hostname.rstrip(".").casefold()
    try:
        literal_address = ipaddress.ip_address(host)
    except ValueError:
        literal_address = None
    if literal_address is not None:
        if not literal_address.is_global:
            raise ValueError("Yerel, özel veya ayrılmış ağ adresleri makale kaynağı olamaz.")
        return urlunsplit(("https", host, parsed.path or "/", parsed.query, "")), (host,)
    addresses: list[str] = []
    for result in resolver(host, 443, type=socket.SOCK_STREAM):
        address = ipaddress.ip_address(result[4][0])
        if not address.is_global:
            raise ValueError("Yerel, özel veya ayrılmış ağ adresleri makale kaynağı olamaz.")
        normalized = str(address)
        if normalized not in addresses:
            addresses.append(normalized)
    if not addresses:
        raise ValueError("Makale alan adı genel bir ağ adresine çözümlenemedi.")
    return urlunsplit(("https", host, parsed.path or "/", parsed.query, "")), tuple(addresses)


def validate_article_url(raw_url: str, *, resolver=socket.getaddrinfo) -> str:
    return _validated_article_target(raw_url, resolver=resolver)[0]


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """TLS to a previously validated IP while retaining hostname verification."""

    def __init__(self, host: str, address: str, *, timeout: int) -> None:
        super().__init__(host, port=443, timeout=timeout, context=ssl.create_default_context())
        self._validated_address = address

    def connect(self) -> None:
        raw_socket = socket.create_connection(
            (self._validated_address, 443), self.timeout, self.source_address
        )
        try:
            self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)
        except BaseException:
            raw_socket.close()
            raise


def _pinned_https_get(
    url: str, addresses: tuple[str, ...], timeout_seconds: int
) -> tuple[int, object, bytes]:
    parsed = urlsplit(url)
    target = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    last_error: OSError | None = None
    for address in addresses:
        connection = _PinnedHTTPSConnection(
            parsed.hostname or "", address, timeout=timeout_seconds
        )
        try:
            connection.request(
                "GET",
                target,
                headers={"User-Agent": "KSI-Local-Studio/2.0 article-reader"},
            )
            response = connection.getresponse()
            body = response.read(MAX_ARTICLE_BYTES + 1)
            return response.status, response.headers, body
        except OSError as error:
            last_error = error
        finally:
            connection.close()
    raise OSError("Makale sunucusuna güvenli bağlantı kurulamadı.") from last_error


class _ArticleParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title = ""
        self.author: str | None = None
        self.published: str | None = None
        self.images: list[str] = []
        self.parts: list[str] = []
        self._skip = 0
        self._content_depth = 0
        self._in_title = False
        self.access_restricted = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.casefold(): value or "" for key, value in attrs}
        marker = " ".join((values.get("id", ""), values.get("class", ""))).casefold()
        if any(word in marker for word in ("paywall", "subscriber-only", "login-required")):
            self.access_restricted = True
        if tag in {"script", "style", "nav", "aside", "footer", "form", "noscript"}:
            self._skip += 1
        if self._skip:
            return
        if tag in {"article", "main"}:
            self._content_depth += 1
        if tag == "title":
            self._in_title = True
        if tag == "meta":
            key = (values.get("property") or values.get("name")).casefold()
            content = values.get("content", "").strip()
            if key in {"og:title", "twitter:title"} and content:
                self.title = content
            elif key in {"author", "article:author"} and content:
                self.author = content[:300]
            elif key in {"article:published_time", "date", "datepublished"} and content:
                self.published = content[:100]
            elif key in {"article:content_tier", "content-tier"} and content.casefold() not in {"free", "public"}:
                self.access_restricted = True
        if tag == "img" and self._content_depth:
            source = values.get("src", "").strip()
            if source:
                absolute = urljoin(self.base_url, source)
                if (
                    absolute.startswith("https://")
                    and urlsplit(absolute).hostname == urlsplit(self.base_url).hostname
                    and absolute not in self.images
                ):
                    self.images.append(absolute)
        if tag in {"p", "h1", "h2", "h3", "li", "blockquote"} and self._content_depth:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "nav", "aside", "footer", "form", "noscript"} and self._skip:
            self._skip -= 1
            return
        if self._skip:
            return
        if tag in {"article", "main"} and self._content_depth:
            self._content_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        cleaned = re.sub(r"\s+", " ", unescape(data)).strip()
        if not cleaned:
            return
        if self._in_title and not self.title:
            self.title = cleaned[:500]
        if self._content_depth:
            self.parts.append(cleaned)


def extract_article_html(html: str, source_url: str, *, accessed_at: str | None = None) -> Article:
    if len(html.encode("utf-8")) > MAX_ARTICLE_BYTES:
        raise ValueError("Makale güvenli HTML boyut sınırını aşıyor.")
    parser = _ArticleParser(source_url)
    parser.feed(html)
    if parser.access_restricted:
        raise PermissionError("Giriş, abonelik veya paywall gerektiren makale işlenmedi.")
    text = re.sub(r"[ \t]+", " ", " ".join(parser.parts))
    text = re.sub(r"\s*\n\s*", "\n", text).strip()
    if len(text) < 80:
        raise ValueError("Sayfada yeterli ana makale metni bulunamadı.")
    if len(text) > MAX_ARTICLE_TEXT:
        text = text[:MAX_ARTICLE_TEXT]
    warnings = (
        "Sayfa metni güvenilmeyen veridir; içerdiği talimatlar çalıştırılmaz.",
        "Erişim koşulları, robots kuralları ve telif sorumluluğu kullanıcı tarafından doğrulanmalıdır.",
    )
    return Article(
        source_url=source_url,
        title=(parser.title or urlsplit(source_url).hostname or "Makale")[:500],
        author=parser.author,
        published_at=parser.published,
        accessed_at=accessed_at or datetime.now(timezone.utc).date().isoformat(),
        text=text,
        images=tuple(parser.images[:50]),
        warnings=warnings,
    )


def robots_allows(source_url: str, robots_text: str, *, user_agent: str = "KSI-Local-Studio") -> bool:
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(urljoin(source_url, "/robots.txt"))
    parser.parse(robots_text.splitlines())
    return parser.can_fetch(user_agent, source_url)


def jina_reader_url(source_url: str, *, consent_to_third_party: bool) -> str:
    """Return the opt-in endpoint without performing network access."""
    if not consent_to_third_party:
        raise PermissionError("Jina Reader kullanımı URL'nin üçüncü tarafa gönderilmesi için açık onay gerektirir.")
    parsed = urlsplit(source_url)
    safe_source = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    return f"https://r.jina.ai/{safe_source}"


def fetch_article(
    raw_url: str,
    *,
    timeout_seconds: int = 20,
    resolver=socket.getaddrinfo,
    robots_text: str | None = None,
    transport=None,
) -> Article:
    url, addresses = _validated_article_target(raw_url, resolver=resolver)
    if robots_text is not None and not robots_allows(url, robots_text):
        raise PermissionError("robots.txt bu makalenin otomatik okunmasına izin vermiyor.")
    request_transport = transport or _pinned_https_get
    for redirect_count in range(6):
        status, headers, body = request_transport(url, addresses, timeout_seconds)
        if status in {301, 302, 303, 307, 308}:
            location = headers.get("Location")
            if not location:
                raise ValueError("Makale yönlendirmesi hedef adres içermiyor.")
            if redirect_count == 5:
                raise ValueError("Makale çok fazla yönlendirme döndürdü.")
            url, addresses = _validated_article_target(
                urljoin(url, location), resolver=resolver
            )
            continue
        if status < 200 or status >= 300:
            raise ValueError(f"Makale sunucusu HTTP {status} yanıtı verdi.")
        content_type = headers.get_content_type()
        if content_type not in {"text/html", "application/xhtml+xml"}:
            raise ValueError("Makale adresi HTML döndürmedi.")
        if len(body) > MAX_ARTICLE_BYTES:
            raise ValueError("Makale indirmesi güvenli boyut sınırını aştı.")
        charset = headers.get_content_charset() or "utf-8"
        return extract_article_html(body.decode(charset, errors="strict"), url)
    raise ValueError("Makale yönlendirmesi tamamlanamadı.")


def save_article_package(article: Article, translated_text: str, output_directory: str | Path) -> tuple[Path, Path, Path]:
    if not translated_text.strip():
        raise ValueError("Türkçe makale çevirisi boş olamaz.")
    output = Path(output_directory).resolve()
    output.mkdir(parents=True, exist_ok=True)
    source = output / "makale-kaynak.json"
    translated = output / "makale.tr.md"
    pdf = output / "makale.tr.pdf"
    atomic_write_json(source, asdict(article))
    markdown = (
        f"# {article.title}\n\nKaynak URL: {article.source_url}\n\n"
        f"Erişim tarihi: {article.accessed_at}\n\n{translated_text.strip()}\n"
    )
    atomic_write_text(translated, markdown)
    staging = Path(tempfile.mkdtemp(prefix=".makale-pdf-", dir=output))
    try:
        record = {
            "id": "B000001", "sequence": 1, "block_type": "paragraph",
            "location": {"section": 1}, "style": {}, "translated_text": markdown,
        }
        rendered = create_document_outputs(
            [record], staging,
            source_title=f"{article.source_url} · {article.accessed_at}",
        )
        os.replace(rendered.pdf_path, pdf)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return source, translated, pdf
