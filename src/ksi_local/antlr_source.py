"""Install the exact pure-Python ANTLR source without running a setup script.

PyPI 4.9.3 has no official wheel. This is explicitly a source overlay, never
an invented official wheel. Original package metadata and BSD notice survive.
"""

import base64
import csv
import io
import tarfile
from email.parser import BytesParser
from pathlib import Path

from ksi_local.atomic_files import atomic_write_bytes, atomic_write_text
from ksi_local.bundle_runtime import digest_file, safe_member


def install_source(archive: Path, notice: Path, packages: Path, pin: dict, notice_pin: dict) -> dict:
    for path, expected in ((archive, pin), (notice, notice_pin)):
        if path.is_symlink() or not path.is_file() or path.stat().st_size != expected["size"] or digest_file(path) != expected["sha256"]:
            raise ValueError("Original ANTLR source or license changed")
    if pin.get("name") != "antlr4-python3-runtime" or pin.get("version") != "4.9.3" or notice_pin.get("version") != "4.9.3":
        raise ValueError("Only the reviewed OmegaConf ANTLR source is supported")
    if packages.is_symlink() or not packages.is_dir():
        raise ValueError("ANTLR requires an explicit fresh prefix package directory")
    metadata_name = "antlr4_python3_runtime-4.9.3.dist-info"
    for name in ("antlr4", metadata_name):
        target = safe_member(packages, name)
        if target.exists() or target.is_symlink():
            raise FileExistsError("ANTLR never replaces an existing package")
    prefix = "antlr4-python3-runtime-4.9.3/"
    plans, seen = [], set()
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        if len(members) > 1000 or sum(row.size for row in members) > 16 * 1024**2:
            raise ValueError("ANTLR source inventory exceeds its bounds")
        for member in members:
            name = member.name.rstrip("/")
            safe_member(packages, name)
            if name == prefix.rstrip("/") and member.isdir():
                continue
            if not name.startswith(prefix) or not (member.isdir() or member.isfile()):
                raise ValueError("ANTLR source has a linked or unexpected member")
            relative = name[len(prefix):]
            if relative == "PKG-INFO":
                target = metadata_name + "/METADATA"
            elif relative.startswith("src/antlr4/") and member.isfile():
                if not relative.endswith(".py"):
                    raise ValueError("ANTLR package contains an unexpected executable/support type")
                target = relative.removeprefix("src/")
            else:
                continue
            if not member.isfile() or member.size > 1024**2 or target.casefold() in seen:
                raise ValueError("ANTLR source member is duplicated or oversized")
            seen.add(target.casefold())
            with source.extractfile(member) as stream:
                content = stream.read(member.size + 1)
            if len(content) != member.size:
                raise ValueError("ANTLR source is truncated")
            plans.append((target, content))
    metadata = next((content for target, content in plans if target.endswith("/METADATA")), None)
    fields = BytesParser().parsebytes(metadata or b"")
    if fields.get("Name") != pin["name"] or fields.get("Version") != pin["version"] or "antlr4/__init__.py" not in seen:
        raise ValueError("Original ANTLR package identity is incomplete")
    plans.append((metadata_name + "/LICENSE.txt", notice.read_bytes()))
    rows = []
    for name, content in plans:
        target = safe_member(packages, name)
        atomic_write_bytes(target, content, mode=0o644)
        checksum = digest_file(target)
        rows.append((name, "sha256=" + base64.urlsafe_b64encode(bytes.fromhex(checksum)).rstrip(b"=").decode(), len(content)))
    record_name = metadata_name + "/RECORD"
    buffer = io.StringIO(newline="")
    csv.writer(buffer).writerows([*rows, (record_name, "", "")])
    atomic_write_text(safe_member(packages, record_name), buffer.getvalue(), mode=0o644)
    names = [name for name, _ in plans] + [record_name]
    return dict(name=pin["name"], version=pin["version"], source_url=pin["url"],
        source_archive_sha256=pin["sha256"], license_sha256=notice_pin["sha256"],
        integration="Original pure-Python sdist overlay; no setup execution or unofficial wheel",
        files=[dict(path=name, sha256=digest_file(safe_member(packages, name))) for name in sorted(names)])
