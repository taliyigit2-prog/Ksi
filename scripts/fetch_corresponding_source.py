#!/usr/bin/env python3
"""Fetch a recipe-pinned source digest with a bound, never trust HEAD sizes.

This build helper preserves the original compressed source. It does not extract
or execute it and does not assert that a license review is complete.
"""

import argparse
import hashlib
import os
import re
import shutil
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from ksi_local.atomic_files import atomic_write_json
from ksi_local.build_inputs import SecureRedirect
from ksi_local.bundle_runtime import digest_file


def fetch_source(url: str, sha256: str, destination: Path, *, limit: int) -> Path:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Corresponding source requires a clean public HTTPS URL.")
    if not re.fullmatch(r"[0-9a-f]{64}", sha256) or not 1 <= limit <= 512 * 1024**2:
        raise ValueError("Corresponding source requires an authoritative SHA-256 and bounded size.")
    if not destination.is_absolute() or destination.is_symlink():
        raise ValueError("Source cache must be an explicit normal absolute path.")
    if destination.exists():
        if destination.is_file() and 0 < destination.stat().st_size <= limit and digest_file(destination) == sha256:
            return destination
        raise FileExistsError("Existing source cache differs; it was not replaced.")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if shutil.disk_usage(destination.parent).free < limit + 512 * 1024**2:
        raise OSError("Source cache has insufficient free space for its declared bound.")
    temporary = destination.with_name("." + destination.name + "." + uuid.uuid4().hex + ".part")
    created = False
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), SecureRedirect())
        request = urllib.request.Request(url, headers={"User-Agent": "KSI-clean-source/1"})
        received, checksum = 0, hashlib.sha256()
        with temporary.open("xb") as output:
            created = True
            with opener.open(request, timeout=45) as response:
                if urlsplit(response.url).scheme != "https":
                    raise ValueError("Corresponding source response is not HTTPS.")
                while block := response.read(1024 * 1024):
                    received += len(block)
                    if received > limit:
                        raise ValueError("Corresponding source exceeds the declared expansion-free download bound.")
                    checksum.update(block)
                    output.write(block)
            output.flush()
            os.fsync(output.fileno())
        if not received or checksum.hexdigest() != sha256:
            raise ValueError("Corresponding source differs from the authoritative recipe digest.")
        os.link(temporary, destination)
        destination.chmod(0o400)
        return destination
    finally:
        if created:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("sha256")
    parser.add_argument("destination", type=Path)
    parser.add_argument("--maximum-mib", type=int, default=128)
    args = parser.parse_args()
    target = fetch_source(args.url, args.sha256, args.destination.absolute(), limit=args.maximum_mib * 1024**2)
    receipt = target.with_name(target.name + ".source.json")
    if not receipt.exists():
        atomic_write_json(receipt, {"schema_version": 1, "source_url": args.url,
            "sha256": args.sha256, "size": target.stat().st_size,
            "digest_verified": True, "redistribution_review_complete": False})
    print("Verified source archive:", target.name, target.stat().st_size, flush=True)


if __name__ == "__main__":
    main()
