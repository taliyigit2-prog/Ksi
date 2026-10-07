"""Do not represent a Rosetta process as native Intel build evidence."""

import ctypes
import errno
import sys


def is_rosetta_translated() -> bool:
    if sys.platform != "darwin":
        return False
    # This MUST be queried within this Python process. A spawned universal
    # sysctl executable may run natively even when its parent is translated.
    libc = ctypes.CDLL(None, use_errno=True)
    query = libc.sysctlbyname
    query.argtypes = [ctypes.c_char_p, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t]
    query.restype = ctypes.c_int
    translated = ctypes.c_int(0)
    size = ctypes.c_size_t(ctypes.sizeof(translated))
    result = query(b"sysctl.proc_translated", ctypes.byref(translated), ctypes.byref(size), None, 0)
    if result != 0:
        if ctypes.get_errno() == errno.ENOENT:
            return False  # The key is absent on non-translated Intel macOS.
        raise RuntimeError("The current native processor state could not be verified.")
    if size.value != ctypes.sizeof(translated) or translated.value not in {0, 1}:
        raise RuntimeError("The current processor translation state is invalid.")
    return bool(translated.value)


def require_native_build_process() -> None:
    if is_rosetta_translated():
        raise ValueError("Rosetta cannot supply native Intel build or acceptance evidence.")
