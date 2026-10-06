import unittest

from ksi_local.native_notices import metadata_member


class NativeNoticeTests(unittest.TestCase):
    def test_only_notice_and_exact_recipe_metadata_selected(self):
        for value in ("info/licenses/LICENSE", "info/recipe/fix.patch", "info/about.json", "info/paths.json"):
            self.assertTrue(metadata_member(value))
        for value in ("lib/private.dylib", "bin/tool", "info/files", "info/test/run_test.py"):
            self.assertFalse(metadata_member(value))

    def test_archive_paths_cannot_escape_notice_tree(self):
        for value in ("/info/licenses/LICENSE", "info/recipe/../../private", "info/recipe/./file", "info/recipe//file", "info\\recipe\\file"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                metadata_member(value)
