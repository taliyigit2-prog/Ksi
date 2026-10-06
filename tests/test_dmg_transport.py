"""DMG staging restrictions do not require native mounts for unit tests."""

import plistlib
import tempfile
import unittest
from pathlib import Path

from ksi_local.dmg_transport import GITHUB_ASSET_LIMIT, build_dmg_transport


class DmgTransportTests(unittest.TestCase):
    def test_asset_bound_is_explicit(self):
        self.assertEqual(GITHUB_ASSET_LIMIT, 2 * 1024**3)

    def test_personal_named_app_is_not_a_public_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = root / "Personal.app"
            app.mkdir()
            with self.assertRaises(ValueError):
                build_dmg_transport(app, root / "dist")

    def test_missing_architecture_is_rejected_before_build_commands(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = root / "KSI Local Studio.app"
            (app / "Contents").mkdir(parents=True)
            (app / "Contents/Info.plist").write_bytes(plistlib.dumps({"CFBundleShortVersionString": "test"}))
            with self.assertRaises(ValueError):
                build_dmg_transport(app, root / "dist")
            self.assertFalse((root / "dist").exists())
