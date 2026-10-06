"""Cross-client workspace readers exclude selection/relocation transactions."""

import contextlib
import fcntl
import os
import stat
from contextvars import ContextVar
from functools import wraps

from ksi_local.job_store import default_database_path


_DESCRIPTOR = ContextVar("ksi_workspace_reader", default=None)


def active_workspace_descriptor():
    return _DESCRIPTOR.get()


@contextlib.contextmanager
def workspace_access(*, mutation=False):
    active = active_workspace_descriptor()
    if active is not None:
        if mutation:
            raise RuntimeError("Aktif iş sırasında çalışma alanı değiştirilemez.")
        yield
        return
    path = default_database_path().parent / "workspace-operation.lock"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    inherited = os.environ.get("KSI_WORKSPACE_LOCK_FD")
    if inherited is not None:
        if mutation:
            raise RuntimeError("İşçi süreci çalışma alanını değiştiremez.")
        try:
            descriptor = int(inherited)
            actual, expected = os.fstat(descriptor), path.stat(follow_symlinks=False)
            if descriptor < 3 or not stat.S_ISREG(actual.st_mode) or (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
                raise ValueError("Geçersiz çalışma alanı işçi kilidi.")
            fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except (OSError, ValueError) as error:
            raise RuntimeError("Çalışma alanı işçi kilidi doğrulanamadı.") from error
        token = _DESCRIPTOR.set(descriptor)
        try:
            yield
        finally:
            _DESCRIPTOR.reset(token)
        return
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    acquired = False
    token = None
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("Çalışma alanı kilidi normal dosya olmalıdır.")
        try:
            fcntl.flock(descriptor, (fcntl.LOCK_EX if mutation else fcntl.LOCK_SH) | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError as error:
            raise RuntimeError("Çalışma alanı başka bir işlem tarafından kullanılıyor.") from error
        if not mutation:
            token = _DESCRIPTOR.set(descriptor)
        yield
    finally:
        if token is not None:
            _DESCRIPTOR.reset(token)
        if acquired:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def workspace_reader(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with workspace_access():
            return function(*args, **kwargs)
    return wrapped
