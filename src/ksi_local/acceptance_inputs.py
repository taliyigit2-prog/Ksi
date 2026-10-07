"""Bounded transfer of exact reviewed inputs; never native acceptance evidence."""
import hashlib
import json
import re
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

from ksi_local.bundle_runtime import digest_file, safe_member
from ksi_local.internal_storage import validate_internal_path

MAX_TRANSFER_BYTES = 32 * 1024**3
MAX_TRANSFER_MEMBERS = 100000


def import_inputs(transport: Path, destination: Path, expected_sha256: str, *, architecture: str) -> dict:
    if architecture not in {"arm64", "x86_64"} or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ValueError("Explicit architecture and reviewed archive SHA-256 required")
    if transport.is_symlink() or not transport.is_dir() or not destination.is_absolute() or destination.exists() or destination.is_symlink():
        raise ValueError("Import requires normal inputs and a new absolute destination")
    validate_internal_path(destination)
    manifest = safe_member(transport, "transfer.json")
    if not manifest.is_file() or manifest.stat().st_size > 1024**2:
        raise ValueError("Transfer manifest is missing or oversized")
    spec = json.loads(manifest.read_bytes())
    if (spec.get("schema_version") != 1 or spec.get("architecture") != architecture
            or spec.get("archive_format") != "tar.gz" or spec.get("archive_sha256") != expected_sha256
            or type(spec.get("archive_bytes")) is not int or not 0 < spec["archive_bytes"] <= MAX_TRANSFER_BYTES):
        raise ValueError("Transport differs from the independently reviewed input")
    parts = spec.get("files")
    if not isinstance(parts, list) or not 1 <= len(parts) <= 32:
        raise ValueError("Transfer part inventory is invalid")
    total, plan = 0, []
    for number, row in enumerate(parts, start=1):
        if not isinstance(row, dict) or row.get("filename") != f"native-intel-inputs.tar.gz.part{number:03d}":
            raise ValueError("Transfer part names/order are invalid")
        if type(row.get("size")) is not int or not 0 < row["size"] < 2 * 1024**3 or not re.fullmatch(r"[0-9a-f]{64}", str(row.get("sha256"))):
            raise ValueError("Transfer part size/digest is invalid")
        part = safe_member(transport, row["filename"])
        if not part.is_file() or part.stat().st_size != row["size"] or digest_file(part) != row["sha256"]:
            raise ValueError("Transfer part differs from its verified bytes")
        total += row["size"]
        plan.append(part)
    if total != spec["archive_bytes"]:
        raise ValueError("Transfer byte count differs")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ksi-reviewed-input-import-", dir=destination.parent) as temporary:
        private = Path(temporary)
        archive = private / "inputs.tar.gz"
        whole = hashlib.sha256()
        with archive.open("xb") as output:
            for part in plan:
                with part.open("rb") as source:
                    while block := source.read(1024**2):
                        output.write(block)
                        whole.update(block)
        if whole.hexdigest() != expected_sha256:
            raise ValueError("Joined transfer does not match independently reviewed SHA-256")
        with tarfile.open(archive) as stream:
            members, expanded_bytes = [], 0
            seen = set()
            for member in stream:
                expanded_bytes += member.size
                if len(members) >= MAX_TRANSFER_MEMBERS or member.size < 0 or expanded_bytes > MAX_TRANSFER_BYTES:
                    raise ValueError("Input archive expansion exceeds bounds")
                name = member.name.rstrip("/")
                parts = PurePosixPath(name).parts
                if (not name or len(name) > 2048 or "\\" in name or "\x00" in name
                        or any(p in {"", ".", ".."} for p in name.split("/")) or name.startswith("/")
                        or not parts or parts[0] not in {"runtime", "components", "input-provenance.json"}
                        or name.casefold() in seen or not (member.isfile() or member.isdir())
                        or member.uid != 0 or member.gid != 0 or member.uname or member.gname
                        or member.mode & 0o7000):
                    raise ValueError("Input archive contains unsafe or identifying metadata")
                seen.add(name.casefold())
                safe_member(private / "expanded", name)
                members.append(member)
            if not members:
                raise ValueError("Input archive is empty")
            expanded = private / "expanded"
            expanded.mkdir()
            stream.extractall(expanded, members=members, filter="data")
        provenance_file = safe_member(expanded, "input-provenance.json")
        if not provenance_file.is_file() or provenance_file.stat().st_size > 1024**2:
            raise ValueError("Input provenance is missing or oversized")
        provenance = json.loads(provenance_file.read_bytes())
        if provenance.get("architecture") != architecture or digest_file(provenance_file) != spec.get("input_provenance_sha256"):
            raise ValueError("Input provenance architecture/digest differs")
        specification_file = safe_member(expanded, "components/component-specification.json")
        if not specification_file.is_file() or specification_file.stat().st_size > 32 * 1024**2:
            raise ValueError("Component specification is missing or oversized")
        components = json.loads(specification_file.read_bytes())
        rows = components.get("files")
        if components.get("architecture") != architecture or not isinstance(rows, list) or not 0 < len(rows) < MAX_TRANSFER_MEMBERS:
            raise ValueError("Component specification architecture/inventory differs")
        component_paths = set()
        for row in rows:
            if (not isinstance(row, dict) or not isinstance(row.get("path"), str)
                    or type(row.get("size")) is not int or row["size"] < 0
                    or not re.fullmatch(r"[0-9a-f]{64}", str(row.get("sha256")))
                    or row["path"].casefold() in component_paths):
                raise ValueError("Component inventory contains an invalid or duplicate row")
            component_paths.add(row["path"].casefold())
            member = safe_member(expanded / "components", row["path"])
            if not member.is_file() or member.stat().st_size != row["size"] or digest_file(member) != row["sha256"]:
                raise ValueError("Extracted component changed before native assembly")
        runtime_file = safe_member(expanded, "runtime/runtime-provenance.json")
        if not runtime_file.is_file() or runtime_file.stat().st_size > 1024**2:
            raise ValueError("Runtime provenance is missing or oversized")
        runtime = json.loads(runtime_file.read_bytes())
        if runtime.get("architecture") != architecture or runtime.get("wheel_lock_sha256") != provenance.get("main_runtime_wheel_lock_sha256"):
            raise ValueError("Runtime and input wheel-lock provenance disagree")
        # Only this importer's own temporary tree is promoted. Existing user
        # directories are never removed or replaced after an error.
        # Exclusive directory creation also protects the last-check race: a
        # competing creator's empty directory must not be replaced by rename.
        destination.mkdir(mode=0o700)
        for child in expanded.iterdir():
            child.rename(destination / child.name)
    return dict(architecture=architecture, files=len(rows), archive_sha256=expected_sha256,
                native_acceptance_performed=False)
