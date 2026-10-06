from __future__ import annotations

import errno
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ksi_local.exporter import _rename_exclusive


class RemovableFilesystemPublishTests(unittest.TestCase):
    def test_exfat_fallback_publishes_without_leaving_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / ".hidden.part"
            destination = root / "original.pdf"
            source.write_bytes(b"verified")
            renamex = MagicMock(return_value=-1)
            library = MagicMock()
            library.renamex_np = renamex
            with (
                patch("ksi_local.exporter.ctypes.CDLL", return_value=library),
                patch("ksi_local.exporter.ctypes.get_errno", return_value=errno.ENOTSUP),
            ):
                _rename_exclusive(source, destination)
            self.assertFalse(source.exists())
            self.assertEqual(destination.read_bytes(), b"verified")

    def test_exfat_fallback_never_replaces_existing_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / ".hidden.part"
            destination = root / "original.pdf"
            source.write_bytes(b"new")
            destination.write_bytes(b"existing")
            renamex = MagicMock(return_value=-1)
            library = MagicMock()
            library.renamex_np = renamex
            with (
                patch("ksi_local.exporter.ctypes.CDLL", return_value=library),
                patch("ksi_local.exporter.ctypes.get_errno", return_value=errno.ENOTSUP),
            ):
                with self.assertRaises(FileExistsError):
                    _rename_exclusive(source, destination)
            self.assertEqual(source.read_bytes(), b"new")
            self.assertEqual(destination.read_bytes(), b"existing")


if __name__ == "__main__":
    unittest.main()
