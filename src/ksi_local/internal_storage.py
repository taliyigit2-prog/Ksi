"""Internal-volume policy shared by GUI, CLI and workers.

No removable-volume discovery is needed for ordinary startup. Checking the
actual filesystem device also rejects an external volume mounted outside the
usual /Volumes directory. Existing user content is never removed here.
"""

import os
from pathlib import Path


def validate_internal_path(path: Path) -> Path:
    raw = path.expanduser().absolute()
    if raw == Path("/") or raw.is_symlink():
        raise RuntimeError("Çalışma alanı normal bir dahili disk klasörü olmalıdır.")
    resolved = raw.resolve()
    if resolved.is_relative_to(Path("/Volumes")):
        raise RuntimeError("KSI çalışma alanı yalnız bilgisayarın dahili diskinde olabilir.")
    # The signed app may not follow a user-planted link to another workspace.
    for parent in raw.parents:
        if parent.is_symlink() and parent not in {Path("/var"), Path("/tmp")}:
            raise RuntimeError("Çalışma alanı yolunda sembolik bağlantı olamaz.")
    existing = resolved
    while not existing.exists():
        if existing == existing.parent:
            raise RuntimeError("Çalışma alanının dahili disk konumu doğrulanamadı.")
        existing = existing.parent
    system_data = Path("/System/Volumes/Data")
    baseline = system_data if system_data.is_dir() else Path.home().resolve()
    if os.stat(existing).st_dev != os.stat(baseline).st_dev:
        raise RuntimeError("Çalışma alanı sistemin dahili veri diskinde olmalıdır.")
    return resolved
