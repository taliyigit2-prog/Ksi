"""One local model operation across desktop, CLI and MCP worker processes."""

from __future__ import annotations

import contextlib
import fcntl
import os
import stat
from contextvars import ContextVar
from functools import wraps
from pathlib import Path

from ksi_local.job_store import default_database_path

_MODEL_DESCRIPTOR: ContextVar[int | None] = ContextVar("ksi_model_descriptor", default=None)


def active_model_descriptor() -> int | None:
    return _MODEL_DESCRIPTOR.get()


@contextlib.contextmanager
def single_model_lock():
    if active_model_descriptor() is not None:
        yield
        return
    path = default_database_path().parent / "model-operation.lock"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    inherited = os.environ.get("KSI_MODEL_LOCK_FD")
    if inherited is not None:
        try:
            inherited_fd = int(inherited)
            actual, expected = os.fstat(inherited_fd), path.stat(follow_symlinks=False)
            if inherited_fd < 3 or not stat.S_ISREG(actual.st_mode) or (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
                raise ValueError("Miras alınan model kilidi geçersiz.")
            fcntl.flock(inherited_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, ValueError) as error:
            raise RuntimeError("Model işçi kilidi doğrulanamadı.") from error
        token = _MODEL_DESCRIPTOR.set(inherited_fd)
        try:
            yield
        finally:
            # Parent owns the shared open-file description; never unlock it here.
            _MODEL_DESCRIPTOR.reset(token)
        return
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    acquired = False
    token = None
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("Model kilidi normal dosya olmalıdır.")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError as error:
            raise RuntimeError("Başka bir yerel model işlemi çalışıyor; bitmesini bekleyin.") from error
        token = _MODEL_DESCRIPTOR.set(descriptor)
        yield
    finally:
        if token is not None:
            _MODEL_DESCRIPTOR.reset(token)
        if acquired:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def serialized_model(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with single_model_lock():
            return function(*args, **kwargs)
    return wrapped
