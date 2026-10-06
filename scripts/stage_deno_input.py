#!/usr/bin/env python3
"""Stage a single verified Deno binary without executing the other architecture."""

import argparse
import json
import stat
import subprocess
import zipfile
from pathlib import Path

from ksi_local.atomic_files import atomic_write_json
from ksi_local.bundle_runtime import digest_file


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("architecture", choices=["arm64", "x86_64"])
    parser.add_argument("archive", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--adhoc-sign", action="store_true", help="Explicitly create a KSI ad-hoc copy from the hash-verified upstream binary; never claim upstream signature success")
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[1]
    entry = json.loads((repository / "config/native-sources.json").read_text())["inputs"]["deno-" + args.architecture]
    if args.archive.is_symlink() or not args.archive.is_file() or args.archive.stat().st_size != entry["size"] or digest_file(args.archive) != entry["sha256"]:
        raise ValueError("Deno archive differs from its official release input.")
    destination = args.destination.absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Deno staging requires a new directory.")
    with zipfile.ZipFile(args.archive) as source:
        members = source.infolist()
        if len(members) != 1 or members[0].filename != "deno" or not 0 < members[0].file_size <= 128 * 1024**2 or stat.S_IFMT(members[0].external_attr >> 16) not in {0, stat.S_IFREG} or members[0].flag_bits & 1:
            raise ValueError("Deno archive is not a single bounded ordinary binary.")
        destination.mkdir(parents=True, mode=0o700)
        target = destination / "deno"
        with source.open(members[0]) as content, target.open("xb") as output:
            received = 0
            while block := content.read(1024 * 1024):
                received += len(block)
                if received > members[0].file_size:
                    raise ValueError("Deno expansion exceeds the pinned archive inventory.")
                output.write(block)
        if received != members[0].file_size:
            raise ValueError("Deno extraction is incomplete.")
    target.chmod(0o755)
    architectures = subprocess.run(["/usr/bin/lipo", "-archs", str(target)], check=True, capture_output=True, text=True, timeout=30).stdout.split()
    if architectures != [args.architecture]:
        raise ValueError("Deno binary does not have the exact target architecture.")
    original_digest = digest_file(target)
    verification = subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(target)], check=False, capture_output=True, timeout=30)
    if not args.adhoc_sign and verification.returncode:
        raise ValueError("Pinned upstream Deno signature is invalid; no signature success was inferred from its release digest.")
    if args.adhoc_sign:
        subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", str(target)], check=True, capture_output=True, timeout=30)
        subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(target)], check=True, capture_output=True, timeout=30)
    record = dict(entry, original_binary_sha256=original_digest,
        upstream_signature_valid=verification.returncode == 0, signing="adhoc" if args.adhoc_sign else "upstream",
        extracted_sha256=digest_file(target), extracted_size=target.stat().st_size,
        acceptance_tested=False, redistribution_review_complete=False)
    atomic_write_json(destination / "tool-staging.json", record, mode=0o644)
    print(json.dumps({"architecture": args.architecture, "bytes": received, "input_verified": True, "acceptance_tested": False}))


if __name__ == "__main__":
    main()
