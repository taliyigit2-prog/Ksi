"""Collect original license/metadata files from exact public wheel inputs.

This is a build evidence inventory, not inferred license approval. Missing
license texts are reported explicitly; metadata labels alone cannot pass review.
"""

import zipfile
from pathlib import Path, PurePosixPath

from ksi_local.atomic_files import atomic_write_bytes, atomic_write_json
from ksi_local.bundle_runtime import digest_file, safe_member
from ksi_local.wheel_lock import validate_wheel_lock


def collect_wheel_notices(lock: dict, wheelhouse: Path, destination: Path) -> dict:
    rows = validate_wheel_lock(lock)
    if not destination.is_absolute() or destination.exists() or destination.is_symlink():
        raise FileExistsError("Wheel notice collection requires new explicit staging.")
    destination.mkdir(parents=True, mode=0o700)
    packages = []
    for row in rows:
        archive = safe_member(wheelhouse, row["filename"])
        if not archive.is_file() or archive.stat().st_size != row["size"] or digest_file(archive) != row["sha256"]:
            raise ValueError("Wheel notice input differs from its official public lock.")
        files, license_present, total = [], False, 0
        with zipfile.ZipFile(archive) as source:
            members = source.infolist()
            if len(members) > 100000:
                raise ValueError("Wheel notice archive contains too many members.")
            seen = set()
            for member in members:
                name = member.filename.rstrip("/")
                safe_member(destination, name)
                if name.casefold() in seen or member.flag_bits & 1:
                    raise ValueError("Wheel archive has ambiguous or encrypted members.")
                seen.add(name.casefold())
                if member.is_dir():
                    continue
                path = PurePosixPath(name)
                basename = path.name.casefold()
                is_license = basename.startswith(("license", "licence", "copying", "notice", "copyright")) or "licenses" in tuple(part.casefold() for part in path.parts)
                is_metadata = basename in {"metadata", "wheel"} and any(part.endswith(".dist-info") for part in path.parts)
                if not (is_license or is_metadata):
                    continue
                if member.file_size > 4 * 1024**2 or total + member.file_size > 64 * 1024**2:
                    raise ValueError("Wheel license metadata exceeds its expansion bound.")
                content = source.read(member)
                content.decode("utf-8")  # Do not publish an opaque binary as a license.
                target = safe_member(destination, row["name"] + "/" + name)
                target.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_bytes(target, content, mode=0o400)
                total += len(content)
                license_present |= is_license and bool(content.strip())
                files.append({"path": target.relative_to(destination).as_posix(), "sha256": digest_file(target),
                    "size": len(content), "kind": "license" if is_license else "metadata"})
        packages.append({"name": row["name"], "version": row["version"], "wheel_sha256": row["sha256"],
            "source_url": row["url"], "declared_license": row["license"],
            "license_text_present": license_present, "files": files})
    result = {"schema_version": 1, "architecture": lock["architecture"], "packages": packages,
        "missing_license_texts": [row["name"] for row in packages if not row["license_text_present"]],
        "redistribution_review_complete": False}
    atomic_write_json(destination / "wheel-notices.json", result, mode=0o644)
    return result
