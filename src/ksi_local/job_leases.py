"""Process-held job leases distinguish live clients from interrupted jobs."""

import contextlib
import fcntl
import os
import hashlib
import stat
from contextvars import ContextVar
from pathlib import Path
from ksi_local.workspace_access import workspace_access


_DESCRIPTOR = ContextVar("ksi_job_lease", default=None)


def active_job_descriptor():
    return _DESCRIPTOR.get()


def _path(state: Path, identifier: str):
    if not isinstance(identifier, str) or not 1 <= len(identifier) <= 4096 or "\x00" in identifier:
        raise ValueError("İş kilidi kimliği geçersiz.")
    root = state / "leases"
    if root.is_symlink():
        raise ValueError("İş kilidi klasörü symlink olamaz.")
    return root / (hashlib.sha256(identifier.encode("utf-8")).hexdigest() + ".lock")


@contextlib.contextmanager
def execution_lease(state: Path, identifier: str):
    with workspace_access():
        with _execution_lease(state, identifier):
            yield


@contextlib.contextmanager
def _execution_lease(state: Path, identifier: str):
    path = _path(state, identifier)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    token = None
    acquired = False
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("İş kilidi normal dosya olmalıdır.")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError as error:
            raise RuntimeError("Bu işi başka bir istemci yürütüyor.") from error
        token = _DESCRIPTOR.set(descriptor)
        yield
    finally:
        if token is not None:
            _DESCRIPTOR.reset(token)
        if acquired:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def lease_is_active(state: Path, identifier: str) -> bool:
    path = _path(state, identifier)
    try:
        descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return False
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("İş kilidi normal dosya olmalıdır.")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        return False
    finally:
        os.close(descriptor)
