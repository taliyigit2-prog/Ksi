"""One local model operation across desktop, CLI and MCP worker processes."""

from __future__ import annotations

import contextlib
import fcntl
import os
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
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    acquired = False
    token = None
    try:
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
