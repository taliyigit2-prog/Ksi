"""Read-only macOS signature and notarization evidence checks."""

from __future__ import annotations

import subprocess
from pathlib import Path


def _run_verifier(argv: tuple[str, ...]) -> bool:
    try:
        completed = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def has_developer_id_signature(package: Path) -> bool:
    if not _run_verifier(("/usr/bin/codesign", "--verify", "--deep", "--strict", str(package))):
        return False
    try:
        details = subprocess.run(
            ("/usr/bin/codesign", "-dv", "--verbose=4", str(package)),
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    evidence = f"{details.stdout}\n{details.stderr}"
    return details.returncode == 0 and "Authority=Developer ID Application:" in evidence


def has_apple_notarization(package: Path) -> bool:
    return _run_verifier(("/usr/bin/xcrun", "stapler", "validate", str(package))) and _run_verifier(
        (
            "/usr/sbin/spctl",
            "--assess",
            "--type",
            "open",
            "--context",
            "context:primary-signature",
            str(package),
        )
    )
