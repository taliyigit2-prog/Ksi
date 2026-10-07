#!/usr/bin/env python3
"""Read-only audit of current GitHub artifacts; never print matched secrets.

Downloads one checksum-bound ZIP at a time into temporary storage. Archives
are inspected without extraction or executing their contents. Reports remain
private build evidence, not public release metadata.
"""

import argparse
import hashlib
import json
import re
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path

from ksi_local.atomic_files import atomic_write_json


PATTERNS = {
    "github-token": rb"\b(?:gh[pousr]|github_pat)_[A-Za-z0-9_]{20,}\b",
    "api-key": rb"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}\b",
    "aws-key": rb"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
    "huggingface-token": rb"\bhf_[A-Za-z0-9]{25,}\b",
    "private-key": rb"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----\r?\n(?:Proc-Type:[^\n]+\nDEK-Info:[^\n]+\n\r?\n)?[A-Za-z0-9+/=\r\n]{64,16384}-----END (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----",
    "credential-url": rb"https?://[^\s/:]+:[^\s/@]+@",
    "personal-home": b"/" + rb"Users/(?!runner(?:/|\b)|you(?:/|\b)|USER(?:/|\b))[^/\s]+/",
}
PATTERNS["current-host-home"] = re.escape(str(Path.home()).encode()) + rb"/"
COMPILED = {name: re.compile(pattern) for name, pattern in PATTERNS.items()}


def scan_stream(stream, name, findings):
    tail = b""
    offset = 0
    found = set()
    total = 0
    while block := stream.read(1024 * 1024):
        total += len(block)
        data = tail + block
        for rule, pattern in COMPILED.items():
            for match in pattern.finditer(data):
                position = offset - len(tail) + match.start()
                key = (rule, position)
                if key not in found:
                    found.add(key)
                    findings.append({"member": name, "rule": rule, "byte_offset": position})
        offset += len(block)
        tail = data[-32768:]
    return total


def inspect_archive(path):
    findings = []
    count = total = 0
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            with archive.open(member) as stream:
                if member.filename.endswith((".tar.gz", ".tgz")):
                    with tarfile.open(fileobj=stream, mode="r|gz") as nested:
                        for item in nested:
                            if not item.isfile():
                                continue
                            count += 1
                            content = nested.extractfile(item)
                            if content is None:
                                raise ValueError("Archive member is unreadable")
                            with content:
                                total += scan_stream(content, member.filename + ":" + item.name, findings)
                else:
                    count += 1
                    total += scan_stream(stream, member.filename, findings)
    return {"files_scanned": count, "bytes_scanned": total, "potential_findings": findings}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository")
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repository):
        parser.error("Expected owner/repository")
    listing = subprocess.check_output(
        ["gh", "api", f"repos/{args.repository}/actions/artifacts?per_page=100", "--paginate", "--slurp"],
        timeout=120,
    )
    artifacts = [item for page in json.loads(listing) for item in page["artifacts"]]
    report = {"schema_version": 1, "repository": args.repository, "artifacts": []}
    for artifact in artifacts:
        if artifact["expired"]:
            report["artifacts"].append({"id": artifact["id"], "expired": True})
            continue
        with tempfile.TemporaryDirectory(prefix="ksi-public-artifact-audit-") as temporary:
            path = Path(temporary) / "artifact.zip"
            with path.open("xb") as output:
                subprocess.run(["gh", "api", f"repos/{args.repository}/actions/artifacts/{artifact['id']}/zip"],
                               stdout=output, check=True, timeout=1800)
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                while block := stream.read(1024 * 1024):
                    digest.update(block)
            expected = artifact.get("digest")
            if expected != "sha256:" + digest.hexdigest():
                raise ValueError("Remote artifact digest does not match downloaded ZIP")
            result = {"id": artifact["id"], "name": artifact["name"], "digest": expected,
                      "source_commit": artifact["workflow_run"]["head_sha"], **inspect_archive(path)}
            report["artifacts"].append(result)
            atomic_write_json(args.report, report, mode=0o600)
            print(json.dumps({"id": artifact["id"], "files_scanned": result["files_scanned"],
                              "potential_findings": len(result["potential_findings"])}), flush=True)
    report["complete"] = len(report["artifacts"]) == len(artifacts)
    atomic_write_json(args.report, report, mode=0o600)


if __name__ == "__main__":
    main()
