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
    parser.add_argument("--engine", choices=["piper", "chatterbox"], default="piper")
    parser.add_argument("--package-only", action="store_true", help="Resolve only the pinned engine wheel; core dependencies have a separate report")
    args = parser.parse_args()
    if args.report.exists() or args.report.is_symlink() or args.python.is_symlink() or not args.python.is_file():
        raise ValueError("Resolver requires an explicit interpreter and a new private report.")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    command = [str(args.python.absolute()), "-m", "pip", "--isolated", "install", "--dry-run", "--ignore-installed", "--no-cache-dir", "--only-binary=:all:", "--index-url", "https://pypi.org/simple", "--python-version", "3.12", "--implementation", "cp", "--abi", "cp312", "--abi", "abi3", "--abi", "none", "--report", str(args.report.absolute())]
    for platform in mac_platforms((14, 0), args.architecture):
        command.extend(["--platform", platform])
    if args.engine == "piper":
        command.append("piper-tts==1.8.0")
    else:
        if args.architecture != "arm64":
            raise ValueError("The selected Chatterbox runtime is native Apple Silicon; Intel uses Piper.")
        if args.package_only:
            command.extend(["--no-deps", "chatterbox-tts==0.1.7"])
        else:
            # Turkish inference only: no Gradio/demo, Chinese segmenter,
            # optional Japanese normalizer or unused training configuration.
            command.extend(["numpy==1.26.4", "torch==2.6.0", "torchaudio==2.6.0",
                "librosa==0.11.0", "s3tokenizer==0.3.0", "numba==0.67.0",
                "transformers==5.2.0", "diffusers==0.29.0", "resemble-perth==1.0.1",
                "conformer==0.3.2", "safetensors==0.5.3", "huggingface-hub==1.31.0",
                "tokenizers==0.22.2"])
    with tempfile.TemporaryDirectory(prefix="ksi-engine-resolve-") as temporary:
        environment = {"HOME": temporary, "PATH": "/usr/bin:/bin", "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"}
        subprocess.run(command, env=environment, check=True, timeout=900)
    os.chmod(args.report, 0o600)


if __name__ == "__main__":
    main()
