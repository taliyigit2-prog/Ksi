#!/usr/bin/env python3
"""Create private resolver reports for an explicitly selected isolated engine.

Reports are not public locks; cross-check them with lock_python_wheels.py.
No environment is installed by this command.
"""

import argparse
import os
import subprocess
import tempfile
from pathlib import Path

from packaging.tags import mac_platforms


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("architecture", choices=["arm64", "x86_64"])
    parser.add_argument("python", type=Path)
    parser.add_argument("report", type=Path)
    parser.add_argument("--engine", choices=["piper"], default="piper")
    args = parser.parse_args()
    if args.report.exists() or args.report.is_symlink() or args.python.is_symlink() or not args.python.is_file():
        raise ValueError("Resolver requires an explicit interpreter and a new private report.")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    command = [str(args.python.absolute()), "-m", "pip", "--isolated", "install", "--dry-run", "--ignore-installed", "--no-cache-dir", "--only-binary=:all:", "--index-url", "https://pypi.org/simple", "--python-version", "3.12", "--implementation", "cp", "--abi", "cp312", "--abi", "abi3", "--abi", "none", "--report", str(args.report.absolute())]
    for platform in mac_platforms((14, 0), args.architecture):
        command.extend(["--platform", platform])
    command.append("piper-tts==1.8.0")
    with tempfile.TemporaryDirectory(prefix="ksi-engine-resolve-") as temporary:
        environment = {"HOME": temporary, "PATH": "/usr/bin:/bin", "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"}
        subprocess.run(command, env=environment, check=True, timeout=900)
    os.chmod(args.report, 0o600)


if __name__ == "__main__":
    main()
