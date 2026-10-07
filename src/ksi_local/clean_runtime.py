"""Assemble a fresh native Python prefix from pinned public build inputs.

This is a build step, not an installer and not a release/acceptance claim.
No existing user virtual environment or application-support tree is read.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from ksi_local.atomic_files import atomic_write_json, atomic_write_text
from ksi_local.build_inputs import extract_python_input
from ksi_local.bundle_runtime import digest_file, host_architecture
from ksi_local.native_processor import require_native_build_process
from ksi_local.wheel_lock import requirements_text, validate_wheel_lock


def assemble_python_runtime(archive: Path, python_input: dict, wheel_lock: dict,
                            wheelhouse: Path, destination: Path) -> dict:
    """Only install on the actual target architecture; never call Rosetta proof."""
    rows = validate_wheel_lock(wheel_lock)
    architecture = wheel_lock["architecture"]
    if host_architecture() != architecture or python_input.get("architecture") != architecture:
        raise ValueError("Clean runtime assembly requires the native target architecture.")
    require_native_build_process()
    if not destination.is_absolute() or destination.exists() or destination.is_symlink():
        raise FileExistsError("The clean runtime destination must be a new absolute directory.")
    if wheelhouse.is_symlink() or not wheelhouse.is_dir():
        raise ValueError("The pinned wheelhouse is missing.")
    names = {row["filename"] for row in rows}
    if {path.name for path in wheelhouse.iterdir()} != names:
        raise ValueError("Wheelhouse contains missing or unlisted artifacts.")
    for row in rows:
        wheel = wheelhouse / row["filename"]
        if wheel.is_symlink() or not wheel.is_file() or wheel.stat().st_size != row["size"] or digest_file(wheel) != row["sha256"]:
            raise ValueError("Pinned wheelhouse integrity failed.")
    python = extract_python_input(archive, destination, sha256=python_input["sha256"])
    # Pip runs without user configuration, index, credentials, source builds,
    # external dependency resolution, bytecode or personal cache access.
    environment = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"}
    with tempfile.TemporaryDirectory(prefix=".ksi-runtime-install-", dir=destination.parent) as temporary:
        private = Path(temporary)
        environment.update(HOME=str(private), TMPDIR=str(private))
        requirement_file = private / "requirements.txt"
        atomic_write_text(requirement_file, requirements_text(wheel_lock))
        subprocess.run([str(python), "-I", "-m", "pip", "--isolated", "install", "--quiet",
                        "--no-index", "--no-deps", "--only-binary=:all:", "--require-hashes",
                        "--no-cache-dir", "--no-compile", "--find-links", str(wheelhouse.absolute()),
                        "-r", str(requirement_file)], env=environment, check=True, timeout=1200)
    # Capture only public input identities, not paths, usernames or host metadata.
    provenance = {"schema_version": 1, "architecture": architecture,
                  "python_version": python_input["version"],
                  "python_source_sha256": python_input["sha256"],
                  "wheel_lock_sha256": __import__("hashlib").sha256(json.dumps(wheel_lock, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                  "packages": [{key: row[key] for key in ("name", "version", "sha256", "license")} for row in rows],
                  "acceptance_tested": False}
    atomic_write_json(destination / "runtime-provenance.json", provenance, mode=0o644)
    return provenance
