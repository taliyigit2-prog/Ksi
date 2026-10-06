from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from ksi_local.catalog import (
    add_private_bookmark,
    load_private_bookmarks,
    load_public_catalog,
    search_public_catalog,
)


ROOT = Path(__file__).resolve().parents[1]


class Phase31Tests(unittest.TestCase):
    def test_bundled_catalog_has_required_multilingual_verified_fields(self) -> None:
        entries = load_public_catalog(ROOT / "config/public-catalog.json")
        self.assertGreaterEqual(len(entries), 4)
        for item in entries:
            self.assertTrue(item.title_tr and item.title_en and item.title_ru)
            self.assertTrue(item.url.startswith("https://"))
            self.assertTrue(item.official)
            self.assertTrue(item.redistribution_verified)
            self.assertTrue(item.verified_at)
            self.assertTrue(item.access_conditions)

    def test_unofficial_or_unverified_public_record_is_rejected(self) -> None:
        original = json.loads((ROOT / "config/public-catalog.json").read_text())
        for field in ("official", "redistribution_verified"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                payload = json.loads(json.dumps(original))
                payload["entries"][0][field] = False
                path = Path(directory) / "catalog.json"
                path.write_text(json.dumps(payload))
                with self.assertRaises(ValueError):
                    load_public_catalog(path)

    def test_offline_search_matches_turkish_english_and_russian(self) -> None:
        entries = load_public_catalog(ROOT / "config/public-catalog.json")
        self.assertEqual(search_public_catalog("programlama", entries)[0].entry_id, "mit-ocw-6-0001")
        self.assertEqual(search_public_catalog("Biology", entries)[0].entry_id, "openstax-biology-2e")
        self.assertEqual(search_public_catalog("Стране", entries)[0].entry_id, "gutenberg-alice-19573")

    def test_private_bookmark_is_mode_600_sanitized_and_never_legal_approval(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private-catalog.local.json"
            item = add_private_bookmark(
                "Kişisel kaynak",
                "https://example.com/book?lang=tr&token=SECRET#page",
                path=path,
                created_at="2026-10-01",
            )
            self.assertEqual(item.url, "https://example.com/book?lang=tr")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            loaded = load_private_bookmarks(path)
            self.assertEqual(loaded[0].legal_status, "not_assessed")
            self.assertFalse(loaded[0].auto_download)
            self.assertNotIn("SECRET", path.read_text())

    def test_private_catalog_is_ignored_and_not_packaged(self) -> None:
        gitignore = (ROOT / ".gitignore").read_text()
        installer = (ROOT / "scripts/install_macos_app.sh").read_text()
        self.assertIn("*.local.json", gitignore)
        self.assertNotIn("private-catalog.local.json", installer)
        self.assertIn("public-catalog.json", installer)
        self.assertFalse((ROOT / "config/private-catalog.local.json").exists())


if __name__ == "__main__":
    unittest.main()
