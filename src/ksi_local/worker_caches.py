"""Private internal JIT caches for dedicated model workers, never app resources."""

import os
import sys
import tempfile
from pathlib import Path

from ksi_local.bundle_runtime import host_architecture
from ksi_local.internal_storage import validate_internal_path
from ksi_local.job_store import default_database_path


def prepare_model_worker_cache() -> Path:
    # Numba captures this setting on import. A dedicated worker must configure
    # it first rather than silently retain an earlier in-tree cache locator.
    if "numba" in sys.modules:
        raise RuntimeError("Model önbelleği motor yüklenmeden önce hazırlanmalıdır.")
    architecture = host_architecture()
    if architecture not in {"arm64", "x86_64"}:
        raise RuntimeError("Model önbelleği işlemci mimarisi desteklenmiyor.")
    state = validate_internal_path(default_database_path().parent)
    if state in {Path("/private/tmp"), Path("/private"), Path("/Users"),
                 Path("/Library"), Path("/System/Volumes/Data"), Path.home().resolve()}:
        raise RuntimeError("Model önbelleği uygulamaya özel bir veri klasörü gerektirir.")
    target = state / "cache" / "numba" / architecture
    for directory in (state, state / "cache", state / "cache/numba", target):
        validate_internal_path(directory)
        if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
            raise RuntimeError("Model önbelleği normal bir dahili klasör olmalıdır.")
        directory.mkdir(mode=0o700, exist_ok=True)
        if directory != state:
            directory.chmod(0o700)
    # The native cache locator otherwise falls back beside its source when a
    # configured directory is unwritable. Fail before importing that engine.
    with tempfile.NamedTemporaryFile(prefix=".ksi-cache-probe-", dir=target) as probe:
        probe.write(b"KSI cache write probe")
        probe.flush()
    os.environ["NUMBA_CACHE_DIR"] = str(target)
    return target
