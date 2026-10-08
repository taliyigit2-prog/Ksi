import ctypes
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ksi_local.bundle_runtime import digest_file
from ksi_local.zstd_build_input import decompress_build_archive


class ZstandardBuildInputTests(unittest.TestCase):
    def fixture(self, root):
        archive, library = root / "synthetic.zst", root / "synthetic.dylib"
        archive.write_bytes(b"Synthetic compressed input, not a real Zstandard frame")
        library.write_bytes(b"Synthetic hash fixture, never execute this file")
        native = MagicMock()
        native.ZSTD_versionNumber.return_value = 10507
        native.ZSTD_createDStream.return_value = 1234
        native.ZSTD_initDStream.return_value = 0
        native.ZSTD_isError.side_effect = lambda value: value == 999
        native.ZSTD_getErrorName.return_value = b"Synthetic decode error"
        def decode(_stream, output, incoming):
            target, source = output._obj, incoming._obj
            ctypes.memmove(target.destination, b"hello", 5)
            target.position = 5
            source.position = source.size
            return 0
        native.ZSTD_decompressStream.side_effect = decode
        return archive, library, native

    def test_exact_library_is_used_with_streamed_output_and_freed_context(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive, library, native = self.fixture(Path(temporary))
            output = io.BytesIO()
            with patch("ksi_local.zstd_build_input.ctypes.CDLL", return_value=native) as loader:
                self.assertEqual(decompress_build_archive(archive, library, digest_file(library), output, maximum_bytes=64), 5)
            loader.assert_called_once_with(str(library))
            self.assertEqual(output.getvalue(), b"hello")
            native.ZSTD_freeDStream.assert_called_once_with(1234)

    def test_changed_or_linked_native_library_rejects_before_loading(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, library, _ = self.fixture(root)
            alias = root / "alias.dylib"
            alias.symlink_to(library)
            for path, digest in ((library, "a" * 64), (alias, digest_file(library))):
                with self.subTest(linked=path == alias), patch("ksi_local.zstd_build_input.ctypes.CDLL") as loader:
                    with self.assertRaises(ValueError):
                        decompress_build_archive(archive, path, digest, io.BytesIO())
                    loader.assert_not_called()

    def test_complete_frame_with_exactly_full_output_buffer_needs_no_extra_decode(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive, library, native = self.fixture(Path(temporary))
            payload = b"x" * (128 * 1024)
            def aligned_frame(_stream, output, incoming):
                ctypes.memmove(output._obj.destination, payload, len(payload))
                output._obj.position = len(payload)
                incoming._obj.position = incoming._obj.size
                return 0
            native.ZSTD_decompressStream.side_effect = aligned_frame
            output = io.BytesIO()
            with patch("ksi_local.zstd_build_input.ctypes.CDLL", return_value=native):
                self.assertEqual(decompress_build_archive(archive, library, digest_file(library), output), len(payload))
            self.assertEqual(output.getvalue(), payload)
            native.ZSTD_decompressStream.assert_called_once()
            native.ZSTD_freeDStream.assert_called_once_with(1234)

    def test_corrupt_truncated_oversized_and_no_progress_streams_fail_and_free(self):
        for mode in ("corrupt", "truncated", "oversized", "no-progress"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                archive, library, native = self.fixture(Path(temporary))
                if mode == "corrupt":
                    native.ZSTD_decompressStream.side_effect = lambda *_args: 999
                elif mode == "truncated":
                    def incomplete(_stream, _output, incoming):
                        incoming._obj.position = incoming._obj.size
                        return 1
                    native.ZSTD_decompressStream.side_effect = incomplete
                elif mode == "no-progress":
                    native.ZSTD_decompressStream.side_effect = lambda *_args: 1
                with patch("ksi_local.zstd_build_input.ctypes.CDLL", return_value=native):
                    with self.assertRaises(ValueError):
                        decompress_build_archive(archive, library, digest_file(library), io.BytesIO(), maximum_bytes=4 if mode == "oversized" else 64)
                native.ZSTD_freeDStream.assert_called_once_with(1234)

    def test_deadline_stays_finite_and_frees_the_owned_context(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive, library, native = self.fixture(Path(temporary))
            with patch("ksi_local.zstd_build_input.ctypes.CDLL", return_value=native), \
                 patch("ksi_local.zstd_build_input.time.monotonic", side_effect=(100, 281)):
                with self.assertRaises(TimeoutError):
                    decompress_build_archive(archive, library, digest_file(library), io.BytesIO())
            native.ZSTD_decompressStream.assert_not_called()
            native.ZSTD_freeDStream.assert_called_once_with(1234)

    def test_foreign_version_and_invalid_budgets_do_not_create_decoder(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive, library, native = self.fixture(Path(temporary))
            native.ZSTD_versionNumber.return_value = 10405
            with patch("ksi_local.zstd_build_input.ctypes.CDLL", return_value=native):
                with self.assertRaisesRegex(ValueError, "1.5.7"):
                    decompress_build_archive(archive, library, digest_file(library), io.BytesIO())
            native.ZSTD_createDStream.assert_not_called()
            for parameters in ({"maximum_bytes": 0}, {"maximum_bytes": 2 * 1024**3 + 1}, {"timeout": 181}):
                with self.subTest(parameters=parameters), patch("ksi_local.zstd_build_input.ctypes.CDLL") as loader:
                    with self.assertRaises(ValueError):
                        decompress_build_archive(archive, library, digest_file(library), io.BytesIO(), **parameters)
                    loader.assert_not_called()
