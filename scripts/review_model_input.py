#!/usr/bin/env python3
"""Bounded first retrieval of narrowly selected official public model artifacts.

This build-only discovery does NOT approve redistribution or replace a public
SHA-256 lock. Its private receipt must be reviewed before normal pinned fetches.
"""

import argparse
import hashlib
import os
import shutil
import urllib.request
import uuid
from pathlib import Path

from ksi_local.atomic_files import atomic_write_json
from ksi_local.build_inputs import SecureRedirect


CANDIDATES = {
    "u2netp": {"url": "https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2netp.onnx", "size": 4574861, "upstream_md5": "8e83ca70e441ab06c318d82300c84806", "digest_source": "rembg U2netpSession legacy MD5; SHA-256 observed at first official HTTPS retrieval", "license_review_complete": False},
    "argos-en-tr": {"url": "https://argos-net.com/v1/translate-en_tr-1_5.argosmodel", "index_revision": "ff90de60728f7c1338ff6b75974e4c89b2442d22", "upstream_ipfs_cid": "QmVfrmu37AjaxyjsdB9AKh2BBBM1q2wur2ewpGeDpqegok", "digest_source": "Official fixed Argos index URL; SHA-256 observed at first HTTPS retrieval, IPFS CID not independently verified", "license_review_complete": False},
    "argos-tr-en": {"url": "https://argos-net.com/v1/translate-tr_en-1_5.argosmodel", "index_revision": "ff90de60728f7c1338ff6b75974e4c89b2442d22", "upstream_ipfs_cid": "QmS6zpzcBmHPqHRBP25trmX8QZm7Tfrb7T7LtLmkCJMSKK", "digest_source": "Official fixed Argos index URL; SHA-256 observed at first HTTPS retrieval, IPFS CID not independently verified", "license_review_complete": False},
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("identifier", choices=sorted(CANDIDATES))
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    destination = args.destination.absolute()
    receipt = destination.with_name(destination.name + ".review.json")
    if destination.exists() or destination.is_symlink() or receipt.exists() or receipt.is_symlink():
        raise FileExistsError("Model review never overwrites an existing artifact or receipt.")
    entry = CANDIDATES[args.identifier]
    maximum = entry.get("size", 512 * 1024**2)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(destination.parent).free < maximum + 512 * 1024**2:
        raise OSError("Not enough disk space for the bounded public model review.")
    temporary = destination.with_name("." + destination.name + "." + uuid.uuid4().hex + ".part")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), SecureRedirect())
    request = urllib.request.Request(entry["url"], headers={"User-Agent": "KSI-clean-build/1"})
    sha256, md5 = hashlib.sha256(), hashlib.md5()
    received = 0
    try:
        with temporary.open("xb") as output, opener.open(request, timeout=45) as response:
            while block := response.read(1024 * 1024):
                received += len(block)
                if received > maximum:
                    raise ValueError("Official model exceeds its review size bound.")
                sha256.update(block)
                md5.update(block)
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
        if not received or ("size" in entry and received != entry["size"]) or ("upstream_md5" in entry and md5.hexdigest() != entry["upstream_md5"]):
            raise ValueError("Official model does not match its upstream legacy integrity metadata.")
        os.link(temporary, destination)
        destination.chmod(0o400)
        record = dict(entry, identifier=args.identifier, sha256=sha256.hexdigest(), size=received, acceptance_tested=False)
        atomic_write_json(receipt, record)
        print(f"Reviewed public input: {args.identifier}, {received} bytes, SHA-256 {sha256.hexdigest()}; redistribution not approved")
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
