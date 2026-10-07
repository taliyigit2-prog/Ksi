import tempfile
import unittest
from pathlib import Path

from ksi_local.distribution_integrity import application_digest


class DistributionIntegrityTests(unittest.TestCase):
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
