import tempfile
import unittest
from pathlib import Path

from ksi_local.app_assembly import copy_clean_tree


class CleanTreeTests(unittest.TestCase):
    def test_internal_link_is_independent_regular_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "input"
            source.mkdir()
            (source / "original.txt").write_text("original")
            (source / "alias.txt").symlink_to("original.txt")
            output = root / "output"
            copy_clean_tree(source, output)
            self.assertFalse((output / "alias.txt").is_symlink())
            self.assertNotEqual((source / "original.txt").stat().st_ino, (output / "alias.txt").stat().st_ino)
            (output / "alias.txt").write_text("changed")
            self.assertEqual((source / "original.txt").read_text(), "original")

    def test_external_link_and_cycle_rejected(self):
        for cyclic in (False, True):
            with self.subTest(cyclic=cyclic), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source = root / "input"
                source.mkdir()
                (root / "outside.txt").write_text("outside")
                (source / "link").symlink_to("." if cyclic else "../outside.txt")
                with self.assertRaises(ValueError):
                    copy_clean_tree(source, root / "output")

    def test_existing_destination_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, output = root / "source", root / "output"
            source.mkdir()
            output.mkdir()
            (output / "keep.txt").write_text("keep")
            with self.assertRaises(ValueError):
                copy_clean_tree(source, output)
            self.assertEqual((output / "keep.txt").read_text(), "keep")

    def test_bytecode_excluded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "__pycache__").mkdir()
            (source / "__pycache__/module.pyc").write_bytes(b"cache")
            (source / "module.py").write_text("pass\n")
            output = root / "output"
            copy_clean_tree(source, output)
            self.assertFalse((output / "__pycache__").exists())
            self.assertTrue((output / "module.py").is_file())
