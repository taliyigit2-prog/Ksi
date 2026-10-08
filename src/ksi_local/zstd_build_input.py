"""Bounded build-only decompression through an explicit hash-verified library."""

import ctypes
import re
import time
from pathlib import Path

from ksi_local.bundle_runtime import digest_file


class _Input(ctypes.Structure):
    _fields_ = [("source", ctypes.c_void_p), ("size", ctypes.c_size_t), ("position", ctypes.c_size_t)]


class _Output(ctypes.Structure):
    _fields_ = [("destination", ctypes.c_void_p), ("size", ctypes.c_size_t), ("position", ctypes.c_size_t)]


def decompress_build_archive(archive: Path, library: Path, library_sha256: str, output,
                             *, maximum_bytes=2 * 1024**3, timeout=180) -> int:
    if (library.is_symlink() or not library.is_file() or not 0 < library.stat().st_size <= 64 * 1024**2
            or not re.fullmatch(r"[0-9a-f]{64}", library_sha256) or digest_file(library) != library_sha256):
        raise ValueError("Zstandard decoding requires the exact reviewed native library.")
    if archive.is_symlink() or not archive.is_file() or type(maximum_bytes) is not int or not 0 < maximum_bytes <= 2 * 1024**3 or not 0 < timeout <= 180:
        raise ValueError("Zstandard source or expansion/deadline budget is invalid.")
    native = ctypes.CDLL(str(library))
    native.ZSTD_versionNumber.argtypes = []
    native.ZSTD_versionNumber.restype = ctypes.c_uint
    if native.ZSTD_versionNumber() != 10507:
        raise ValueError("The reviewed build decoder must be Zstandard 1.5.7.")
    native.ZSTD_createDStream.argtypes = []
    native.ZSTD_createDStream.restype = ctypes.c_void_p
    native.ZSTD_initDStream.argtypes = [ctypes.c_void_p]
    native.ZSTD_initDStream.restype = ctypes.c_size_t
    native.ZSTD_decompressStream.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Output), ctypes.POINTER(_Input)]
    native.ZSTD_decompressStream.restype = ctypes.c_size_t
    native.ZSTD_freeDStream.argtypes = [ctypes.c_void_p]
    native.ZSTD_freeDStream.restype = ctypes.c_size_t
    native.ZSTD_isError.argtypes = [ctypes.c_size_t]
    native.ZSTD_isError.restype = ctypes.c_uint
    native.ZSTD_getErrorName.argtypes = [ctypes.c_size_t]
    native.ZSTD_getErrorName.restype = ctypes.c_char_p
    def checked(value):
        if native.ZSTD_isError(value):
            detail = native.ZSTD_getErrorName(value).decode("ascii", errors="replace")[:120]
            raise ValueError("Original Zstandard archive is invalid: " + detail)
        return value
    stream = native.ZSTD_createDStream()
    if not stream:
        raise MemoryError("Native Zstandard decoder could not allocate its bounded stream.")
    total, remaining = 0, 1
    started = time.monotonic()
    try:
        checked(native.ZSTD_initDStream(stream))
        target = ctypes.create_string_buffer(128 * 1024)
        with archive.open("rb") as compressed:
            while block := compressed.read(128 * 1024):
                source = ctypes.create_string_buffer(block)
                input_buffer = _Input(ctypes.addressof(source), len(block), 0)
                while True:
                    if time.monotonic() - started > timeout:
                        raise TimeoutError("Original archive decoding exceeded its build deadline.")
                    previous = input_buffer.position
                    output_buffer = _Output(ctypes.addressof(target), len(target), 0)
                    remaining = checked(native.ZSTD_decompressStream(stream, ctypes.byref(output_buffer), ctypes.byref(input_buffer)))
                    total += output_buffer.position
                    if total > maximum_bytes:
                        raise ValueError("Original archive exceeds its bounded expansion budget.")
                    if output_buffer.position:
                        output.write(target.raw[:output_buffer.position])
                    if input_buffer.position == input_buffer.size and output_buffer.position < len(target):
                        break
                    if previous == input_buffer.position and not output_buffer.position:
                        raise ValueError("Original archive decoder made no progress.")
        if remaining != 0 or not total:
            raise ValueError("Original Zstandard archive is empty or truncated.")
        return total
    finally:
        native.ZSTD_freeDStream(stream)
