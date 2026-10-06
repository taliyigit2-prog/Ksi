"""Every redesigned native screen has explicit text in every supported locale."""

import unittest

from ksi_local.i18n import SUPPORTED_UI_LANGUAGES
from ksi_local.ui.strings import COPY, NAV_KEYS, text, validate_catalog


class StudioStringTests(unittest.TestCase):
    def test_all_catalog_columns_are_complete(self):
        validate_catalog()
        for locale in SUPPORTED_UI_LANGUAGES:
            for key in (*NAV_KEYS, *COPY):
                with self.subTest(locale=locale, key=key):
                    self.assertTrue(text(key, locale).strip())

    def test_unknown_locale_falls_back_to_english(self):
        self.assertEqual(text("download", "unknown"), "Download")
