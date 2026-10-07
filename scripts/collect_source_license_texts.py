#!/usr/bin/env python3
"""Preserve bounded original license texts from one exact source archive.

The full compressed corresponding source remains intact; no source code is
executed or unpacked wholesale. This collector is not legal-review approval.
"""

import argparse
import json
import tarfile
from pathlib import Path

from ksi_local.atomic_files import atomic_write_bytes, atomic_write_json
from ksi_local.bundle_runtime import digest_file, safe_member


def collect(archive: Path, sha256: str, destination: Path, *, members: tuple[str, ...] = ()) -> dict:
    if archive.is_symlink() or not archive.is_file() or digest_file(archive) != sha256:
        raise ValueError("License source archive differs from its reviewed public digest.")
    if not destination.is_absolute() or destination.exists() or destination.is_symlink():
        raise FileExistsError("Source license collection requires new explicit staging.")
    files, total = [], 0
    requested = set(members)
    if len(requested) != len(members):
        raise ValueError("Original notice selection contains duplicate paths.")
    for name in requested:
        safe_member(destination, name)
    with tarfile.open(archive, "r:*") as source:
        members = source.getmembers()
        if len(members) > 100000 or sum(row.size for row in members) > 4 * 1024**3:
            raise ValueError("Source archive exceeds bounded inventory expansion.")
        selected, seen = [], set()
        for row in members:
            name = row.name.removeprefix("./").rstrip("/")
            if not name:
                continue
            safe_member(destination, name)
            basename = Path(name).name.casefold()
            wanted = "license" in basename or "licence" in basename or basename.startswith(("copying", "copyright", "notice")) or "/LICENSES/" in name
            # Qt's "licensewizard" examples include images and executable
            # tutorial code, not legal notices. Do not mistake names for text.
            if Path(name).suffix.casefold() in {".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp", ".cpp", ".c", ".h", ".hpp", ".cxx", ".cc", ".py", ".qml", ".ui", ".qrc", ".rs", ".go", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt", ".cs", ".swift", ".m", ".mm", ".sh", ".cmake"}:
                wanted = False
            if Path(name).suffix.casefold() in {".a", ".lib", ".o", ".obj", ".rlib", ".dll", ".so", ".dylib", ".exe", ".wasm", ".bin", ".class", ".jar", ".pdf", ".gz", ".zip"}:
                wanted = False
            if requested:
                wanted = wanted and name in requested
            if not wanted or row.isdir():
                continue
            if not row.isfile() or row.size > 4 * 1024**2 or name.casefold() in seen or total + row.size > 128 * 1024**2:
                raise ValueError("Source license member is linked, duplicated or oversized.")
            seen.add(name.casefold())
            total += row.size
            selected.append((row, name))
        if not selected:
            raise ValueError("Source archive has no original license files to preserve.")
        if requested and {name for _, name in selected} != requested:
            raise ValueError("An explicitly selected original notice is missing or is not legal text.")
        destination.mkdir(parents=True, mode=0o700)
        for row, name in selected:
            stream = source.extractfile(row)
            if stream is None:
                raise ValueError("Source license cannot be read.")
            with stream:
                content = stream.read(row.size + 1)
            if len(content) != row.size:
                raise ValueError("Source license is incomplete.")
            content.decode("utf-8")
            target = safe_member(destination, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(target, content, mode=0o400)
            files.append({"path": name, "sha256": digest_file(target), "size": len(content)})
    result = {"schema_version": 1, "source_archive_sha256": sha256,
        "source_archive_name": archive.name, "files": files, "redistribution_review_complete": False}
    atomic_write_json(destination / "source-licenses.json", result, mode=0o644)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("sha256")
    parser.add_argument("destination", type=Path)
    parser.add_argument("--member", action="append", default=[], help="Exact original notice path; excludes unrelated test-fixture licenses.")
    args = parser.parse_args()
    result = collect(args.archive, args.sha256, args.destination.absolute(), members=tuple(args.member))
    print(json.dumps({"license_files": len(result["files"]), "redistribution_review_complete": False}))


if __name__ == "__main__":
    main()
