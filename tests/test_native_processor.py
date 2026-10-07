import ctypes
import errno
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ksi_local.native_processor import is_rosetta_translated, require_native_build_process


class NativeProcessorTests(unittest.TestCase):
    def query(self, *, value=0, result=0):
        def call(name, state, size, new_value, new_size):
            self.assertEqual(name, b"sysctl.proc_translated")
            ctypes.cast(state, ctypes.POINTER(ctypes.c_int)).contents.value = value
            return result
        return SimpleNamespace(sysctlbyname=call)

    def test_translation_is_queried_in_the_current_process(self):
        with patch("ksi_local.native_processor.sys.platform", "darwin"), patch("ksi_local.native_processor.ctypes.CDLL", return_value=self.query(value=1)) as library:
            self.assertTrue(is_rosetta_translated())
            library.assert_called_once_with(None, use_errno=True)

    def test_native_process_and_absent_intel_key_are_not_translated(self):
        with patch("ksi_local.native_processor.sys.platform", "darwin"), patch("ksi_local.native_processor.ctypes.CDLL", return_value=self.query()):
            self.assertFalse(is_rosetta_translated())
        with (patch("ksi_local.native_processor.sys.platform", "darwin"),
                patch("ksi_local.native_processor.ctypes.CDLL", return_value=self.query(result=-1)),
                patch("ksi_local.native_processor.ctypes.get_errno", return_value=errno.ENOENT)):
            self.assertFalse(is_rosetta_translated())

    def test_unknown_state_and_permission_error_do_not_pass_native_gate(self):
        with patch("ksi_local.native_processor.sys.platform", "darwin"), patch("ksi_local.native_processor.ctypes.CDLL", return_value=self.query(value=2)):
            with self.assertRaises(RuntimeError):
                is_rosetta_translated()
        with (patch("ksi_local.native_processor.sys.platform", "darwin"),
                patch("ksi_local.native_processor.ctypes.CDLL", return_value=self.query(result=-1)),
                patch("ksi_local.native_processor.ctypes.get_errno", return_value=errno.EPERM)):
            with self.assertRaises(RuntimeError):
                is_rosetta_translated()

    def test_rosetta_cannot_promote_build_to_native_evidence(self):
        with patch("ksi_local.native_processor.is_rosetta_translated", return_value=True):
            with self.assertRaisesRegex(ValueError, "Rosetta"):
                require_native_build_process()
