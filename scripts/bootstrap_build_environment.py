#!/usr/bin/env python3
"""Create the build helper interpreter using only pinned public inputs.

Needs only the system Python standard library and the committed source tree.
This is not the shipped application runtime.
"""

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

from ksi_local.atomic_files import atomic_write_text
from ksi_local.build_inputs import extract_python_input, fetch_pinned_input
from ksi_local.bundle_runtime import host_architecture


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    architecture = host_architecture()
    sources = json.loads((root / "config/runtime-sources.json").read_text(encoding="utf-8"))
    python_input = sources["inputs"]["python-" + architecture]
    lock = json.loads((root / f"config/python-wheels-{architecture}.json").read_text(encoding="utf-8"))
    packaging = next(row for row in lock["wheels"] if row["name"].casefold() == "packaging")
    cache = root / "build/cache"
    archive = fetch_pinned_input(python_input, cache / f'python-{architecture}-{python_input["version"]}.tar.gz')
    interpreter = extract_python_input(archive, args.destination.absolute(), sha256=python_input["sha256"])
    wheel = fetch_pinned_input(packaging, cache / packaging["filename"])
    with tempfile.TemporaryDirectory(prefix=".ksi-bootstrap-home-", dir=args.destination.absolute().parent) as temporary:
        environment = {"HOME": temporary, "TMPDIR": temporary, "PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"}
        requirements = Path(temporary) / "requirements.txt"
        atomic_write_text(requirements, f'{wheel.as_uri()} --hash=sha256:{packaging["sha256"]}\n')
        subprocess.run([str(interpreter), "-I", "-m", "pip", "--isolated", "install", "--no-index", "--no-deps", "--require-hashes", "--no-cache-dir", "--no-compile", "-r", str(requirements)], env=environment, check=True, timeout=180)
    print(json.dumps({"architecture": architecture, "python_version": python_input["version"], "bootstrap_complete": True, "acceptance_tested": False}))


if __name__ == "__main__":
    main()
