import hashlib
import io
import tarfile
import tempfile
import unittest
from pathlib import Path

from ksi_local.antlr_source import install_source


class AntlrSourceTests(unittest.TestCase):
    def fixture(self, root, extra=None):
        archive, notice = root / "source.tar.gz", root / "notice.txt"
        contents = {
            "antlr4-python3-runtime-4.9.3/PKG-INFO": b"Metadata-Version: 2.1\nName: antlr4-python3-runtime\nVersion: 4.9.3\n\nSynthetic fixture metadata\n",
            "antlr4-python3-runtime-4.9.3/src/antlr4/__init__.py": b"raise RuntimeError('Synthetic source must never be executed during installation')\n",
        }
        if extra:
            contents.update(extra)
        with tarfile.open(archive, "w:gz") as target:
            for name, content in contents.items():
                member = tarfile.TarInfo(name)
                member.size = len(content)
                target.addfile(member, io.BytesIO(content))
        notice.write_bytes(b"Synthetic original notice fixture, not a legal grant")
        def pin(path):
            return dict(size=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest(), version="4.9.3")
        source_pin = dict(pin(archive), name="antlr4-python3-runtime", url="https://files.pythonhosted.org/synthetic-source.tar.gz")
        packages = root / "packages"
        packages.mkdir()
        return archive, notice, packages, source_pin, pin(notice)

    def test_original_sources_metadata_and_notice_survive_without_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.fixture(Path(directory))
            result = install_source(*args)
            self.assertEqual(result["name"], "antlr4-python3-runtime")
            self.assertEqual(result["source_archive_sha256"], args[3]["sha256"])
            metadata = args[2] / "antlr4_python3_runtime-4.9.3.dist-info"
            self.assertEqual((metadata / "LICENSE.txt").read_bytes(), args[1].read_bytes())
            self.assertIn("Name: antlr4-python3-runtime", (metadata / "METADATA").read_text())
            self.assertEqual(len(result["files"]), 4)
            with self.assertRaises(FileExistsError):
                install_source(*args)

    def test_changed_source_or_notice_is_rejected_before_installation(self):
        for changed in (0, 1):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as directory:
                args = self.fixture(Path(directory))
                args[changed].write_bytes(b"Changed synthetic source/notice")
                with self.assertRaises(ValueError):
                    install_source(*args)
                self.assertEqual(list(args[2].iterdir()), [])

    def test_traversal_and_non_python_package_payloads_are_rejected(self):
        for name in ("../escaped.py", "antlr4-python3-runtime-4.9.3/src/antlr4/injection.pth"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                args = self.fixture(Path(directory), {name: b"Synthetic forbidden member"})
                with self.assertRaises(ValueError):
                    install_source(*args)
                self.assertEqual(list(args[2].iterdir()), [])

    def test_unreviewed_package_identity_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.fixture(Path(directory))
            args[3]["name"] = "other-package"
            with self.assertRaises(ValueError):
                install_source(*args)
