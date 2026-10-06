from __future__ import annotations

import unittest

from ksi_local.system_health import _major_version, _version_tuple


class VersionParsingTests(unittest.TestCase):
    def test_parses_common_version_lines(self) -> None:
        self.assertEqual(_major_version("v24.12.0"), 24)
        self.assertEqual(_major_version("deno 2.3.1"), 2)
        self.assertEqual(_major_version("ffmpeg version 9.0.1"), 9)

    def test_returns_none_for_unknown_text(self) -> None:
        self.assertIsNone(_major_version("version unknown"))
        self.assertIsNone(_major_version(None))

    def test_parses_minor_version_for_minimum_checks(self) -> None:
        self.assertEqual(_version_tuple("deno 2.3.0"), (2, 3, 0))
        self.assertLess(_version_tuple("deno 2.2.9"), (2, 3, 0))


if __name__ == "__main__":
    unittest.main()
