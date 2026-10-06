"""Optional macOS copy-on-write clone; never a hard link to mutable user data."""

import ctypes
import errno
import os
import sys
from pathlib import Path


def clone_file(source: Path, destination: Path) -> bool:
    """Return False only when cloning is unavailable; never replace a target."""
    if source.is_symlink() or not source.is_file() or destination.is_symlink() or destination.exists():
        raise FileExistsError("Kopyalama girdisi veya yeni hedefi güvenli değil.")
    if sys.platform != "darwin" or source.stat().st_dev != destination.parent.stat().st_dev:
        return False
    library = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    function = getattr(library, "clonefile", None)
    if function is None:
        return False
    function.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int)
    function.restype = ctypes.c_int
    result = function(os.fsencode(source), os.fsencode(destination), 0)
    if result == 0:
        return True
    error = ctypes.get_errno()
    if error in {errno.EXDEV, errno.ENOTSUP, errno.EINVAL, errno.ENOSYS} and not destination.exists() and not destination.is_symlink():
        return False
    raise OSError(error, "Yazıldığında ayrışan model kopyası oluşturulamadı.")
