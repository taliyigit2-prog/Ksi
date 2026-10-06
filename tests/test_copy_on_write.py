"""A supported clone must be an independent file, never a mutable hard link."""

import tempfile
import unittest
from pathlib import Path

from ksi_local.copy_on_write import clone_file


class CopyOnWriteTests(unittest.TestCase):
    def test_clone_preserves_source_after_target_edit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, target = root / "source", root / "target"
            source.write_bytes(b"original")
            if not clone_file(source, target):
                self.skipTest("Filesystem has no copy-on-write clone support")
            self.assertNotEqual(source.stat().st_ino, target.stat().st_ino)
            target.write_bytes(b"changed")
            self.assertEqual(source.read_bytes(), b"original")

    def test_existing_destination_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, target = root / "source", root / "target"
            source.write_bytes(b"source")
            target.write_bytes(b"user data")
            with self.assertRaises(FileExistsError):
                clone_file(source, target)
            self.assertEqual(target.read_bytes(), b"user data")
