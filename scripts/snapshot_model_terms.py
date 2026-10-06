#!/usr/bin/env python3
"""Snapshot official model terms as inert offline text, not an acceptance record.

The mutable official page is retrieved without credentials. Observed hashes and
date are recorded truthfully; no upstream immutable digest or legal review is
invented. Preserve the original packaged license alongside this newer notice.
"""

import argparse
import hashlib
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

from ksi_local.atomic_files import atomic_write_bytes, atomic_write_json, atomic_write_text
from ksi_local.build_inputs import SecureRedirect


PAGES = {"gemma-terms": "https://ai.google.dev/gemma/terms",
         "gemma-prohibited-use": "https://ai.google.dev/gemma/prohibited_use_policy"}


class ArticleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.ignored = 0
        self.parts = []

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if not self.depth and "devsite-article-body" in attrs.get("class", "").split():
            self.depth = 1
            return
        if self.depth and tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.depth += 1
        if self.depth and tag in {"script", "style"}:
            self.ignored += 1
        if self.depth and tag in {"p", "li", "h1", "h2", "h3", "h4", "br"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            return
        if not self.depth:
            return
        if tag in {"script", "style"} and self.ignored:
            self.ignored -= 1
        self.depth -= 1
        if tag in {"p", "li", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_startendtag(self, tag, attributes):
        if self.depth and tag in {"br", "hr"}:
            self.parts.append("\n")

    def handle_data(self, content):
        if self.depth and not self.ignored:
            self.parts.append(content)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("identifier", choices=sorted(PAGES))
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    destination = args.destination.absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Model terms snapshot requires new staging.")
    url = PAGES[args.identifier]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), SecureRedirect())
    request = urllib.request.Request(url, headers={"User-Agent": "KSI-model-notices/1", "Accept-Language": "en"})
    with opener.open(request, timeout=45) as response:
        raw = response.read(2 * 1024**2 + 1)
        if len(raw) > 2 * 1024**2 or response.url != url:
            raise ValueError("Official model terms response exceeds its bound or changes origin.")
    article = ArticleText()
    article.feed(raw.decode("utf-8"))
    content = "\n".join(" ".join(line.split()) for line in "".join(article.parts).splitlines() if line.strip())
    if not 500 < len(content.encode("utf-8")) <= 256 * 1024 or "Gemma" not in content:
        raise ValueError("Official model terms article could not be extracted completely.")
    if args.identifier == "gemma-terms" and ("TranslateGemma" not in content or "Section 4" not in content):
        raise ValueError("Current official terms do not include the selected model or required sections.")
    destination.mkdir(parents=True, mode=0o700)
    atomic_write_bytes(destination / "official-page.html", raw, mode=0o400)
    text = args.identifier + "\nSource: " + url + "\n\n" + content + "\n"
    target = destination / (args.identifier + ".txt")
    atomic_write_text(target, text, mode=0o400)
    record = {"schema_version": 1, "source_url": url,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "digest_source": "Observed official HTTPS page and inert article snapshot; no immutable upstream hash published",
        "original_page_sha256": hashlib.sha256(raw).hexdigest(),
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "text_size": target.stat().st_size, "license_acceptance_record": False,
        "redistribution_review_complete": False}
    atomic_write_json(destination / "snapshot.json", record)
    print(args.identifier, record["text_size"], record["text_sha256"])


if __name__ == "__main__":
    main()
