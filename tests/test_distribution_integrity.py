import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from ksi_local.distribution_integrity import application_digest, verify_embedded_application


class DistributionIntegrityTests(unittest.TestCase):
    def test_embedded_large_app_still_requires_strict_deep_signature(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            app = root / "KSI Local Studio.app"
            app.mkdir()
            (app / "synthetic-payload.txt").write_text("Synthetic signature budget fixture, not a native app")
            (root / "Applications").symlink_to("/Applications")
            @contextmanager
            def mounted(_image):
                yield root
            with patch("ksi_local.distribution_integrity.mounted_distribution", mounted), \
                 patch("ksi_local.distribution_integrity.subprocess.run") as command:
                result = verify_embedded_application(root / "synthetic.dmg", app)
            self.assertTrue(result["embedded_app_verified"])
            self.assertEqual(command.call_args.kwargs["timeout"], 900)
            self.assertTrue(command.call_args.kwargs["check"])
            self.assertIn("--strict", command.call_args.args[0])
            self.assertIn("--deep", command.call_args.args[0])

    def test_launcher_and_outer_code_seal_are_both_covered(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary) / "KSI Local Studio.app"
            (app / "Contents/MacOS").mkdir(parents=True)
            launcher = app / "Contents/MacOS/KSI-Local-Studio"
            launcher.write_bytes(b"fixture")
            first = application_digest(app)
            launcher.chmod(0o755)
            self.assertNotEqual(first, application_digest(app))
            first = application_digest(app)
            (app / "Contents/_CodeSignature").mkdir()
            (app / "Contents/_CodeSignature/CodeResources").write_bytes(b"fixture seal")
            self.assertNotEqual(first, application_digest(app))

    def test_linked_member_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary) / "KSI Local Studio.app"
            app.mkdir()
            (app / "linked").symlink_to("/Applications")
            with self.assertRaises(ValueError):
                application_digest(app)
