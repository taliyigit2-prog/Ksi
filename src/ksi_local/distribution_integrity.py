"""Inspect native read-only installation media, not manifest claims alone."""

import hashlib
import json
import plistlib
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

from ksi_local.bundle_runtime import digest_file


def application_digest(app: Path) -> str:
    """Cover all app members, including the launcher and outer code seal."""
    if app.is_symlink() or not app.is_dir() or app.name != "KSI Local Studio.app":
        raise ValueError("Application inventory requires a normal product bundle")
    digest = hashlib.sha256()
    count = 0
    for path in sorted(app.rglob("*")):
        if path.is_symlink():
            raise ValueError("Application inventory cannot contain symbolic links")
        count += 1
        if count > 150000:
            raise ValueError("Application inventory exceeds its member bound")
        if not path.is_file() and not path.is_dir():
            raise ValueError("Application inventory contains a special file")
        row = {"path": path.relative_to(app).as_posix(),
               "kind": "file" if path.is_file() else "directory"}
        if path.is_file():
            row.update(size=path.stat().st_size, sha256=digest_file(path),
                       executable=bool(path.stat().st_mode & 0o111))
        digest.update(json.dumps(row, sort_keys=True, separators=(",", ":")).encode() + b"\n")
    if count == 0:
        raise ValueError("Application inventory is empty")
    return digest.hexdigest()


@contextmanager
def mounted_distribution(image: Path):
    """Always mount at an owned temporary location and detach on exit."""
    if image.is_symlink() or not image.is_file() or image.suffix != ".dmg":
        raise ValueError("Installation media must be a normal primary DMG")
    subprocess.run(["/usr/bin/hdiutil", "verify", str(image)], check=True,
                   capture_output=True, timeout=10800)
    with tempfile.TemporaryDirectory(prefix="ksi-install-media-") as temporary:
        mount = Path(temporary).resolve() / "volume"
        mount.mkdir()
        attached = False
        try:
            result = subprocess.run(["/usr/bin/hdiutil", "attach", "-readonly", "-nobrowse",
                                     "-plist", "-mountpoint", str(mount), str(image)],
                                    check=True, capture_output=True, timeout=600)
            attached = True
            entities = plistlib.loads(result.stdout).get("system-entities", [])
            if not any(isinstance(row.get("mount-point"), str)
                       and Path(row["mount-point"]).resolve() == mount for row in entities):
                raise ValueError("Installation media did not mount at the requested location")
            yield mount
        finally:
            if attached:
                # No force-detach: a busy mount must be reported, not hidden.
                subprocess.run(["/usr/bin/hdiutil", "detach", str(mount)], check=True,
                               capture_output=True, timeout=120)


def verify_embedded_application(image: Path, application: Path) -> dict:
    expected = application_digest(application)
    with mounted_distribution(image) as mount:
        embedded = mount / application.name
        actual = application_digest(embedded)
        if actual != expected:
            raise ValueError("DMG does not contain the exact accepted application")
        subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(embedded)],
                       check=True, capture_output=True, timeout=900)
        shortcut = mount / "Applications"
        if not shortcut.is_symlink() or str(shortcut.readlink()) != "/Applications":
            raise ValueError("DMG is missing its standard Applications installation shortcut")
    return {"application_tree_sha256": expected, "embedded_app_verified": True}
