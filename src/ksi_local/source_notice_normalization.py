"""Reclassify empty source placeholders, never waive license requirements."""
import json
import tarfile
from pathlib import Path
from urllib.parse import urlsplit

from ksi_local.atomic_files import atomic_write_json
from ksi_local.bundle_runtime import digest_file, safe_member


def normalize_empty_source_notices(components: Path) -> dict:
    """Require exact retained archive and nonempty sibling grant before mutation."""
    if not components.is_absolute() or components.is_symlink() or not components.is_dir():
        raise ValueError("Notice normalization needs an ordinary explicit build root")
    metadata = safe_member(components, "component-specification.json")
    if metadata.is_symlink() or not metadata.is_file() or metadata.stat().st_size > 32 * 1024**2:
        raise ValueError("Notice inventory is missing, linked or oversized")
    before = digest_file(metadata)
    specification = json.loads(metadata.read_bytes())
    rows = specification.get("files")
    if specification.get("schema_version") != 1 or not isinstance(rows, list) or not 1 <= len(rows) <= 100000:
        raise ValueError("Notice inventory is malformed")
    if len({row["path"].casefold() for row in rows}) != len(rows):
        raise ValueError("Notice paths are duplicated")
    changed = []
    for row in rows:
        if row.get("role") != "license" or row.get("size") != 0:
            continue
        identifier = row["identifier"]
        if any(other.get("license_file") == identifier for other in rows) or any(
                identifier in model.get("notices", []) for model in specification.get("models", [])):
            raise ValueError("An empty required license cannot be reclassified")
        url = row.get("source_url", "")
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Empty notice lacks clean original public provenance")
        path = safe_member(components, row["path"])
        if not row["path"].startswith("licenses/") or path.is_symlink() or not path.is_file() or path.stat().st_size != 0 or digest_file(path) != row["sha256"]:
            raise ValueError("Empty notice bytes differ from inventory")
        candidates = [item for item in rows if item.get("role") == "support"
            and item.get("source_url") == url and item.get("sha256") == row.get("revision")
            and item["path"].startswith("sources/")]
        if len(candidates) != 1:
            raise ValueError("Empty notice needs exactly one original digest-bound source archive")
        source = candidates[0]
        archive = safe_member(components, source["path"])
        if archive.is_symlink() or not archive.is_file() or archive.stat().st_size != source["size"] or digest_file(archive) != source["sha256"]:
            raise ValueError("Original source archive changed")
        siblings = [item for item in rows if item.get("role") == "license" and item.get("size", 0) > 0
            and item.get("source_url") == url and item.get("revision") == row.get("revision")]
        if not siblings:
            raise ValueError("Empty notice cannot replace missing original legal text")
        with tarfile.open(archive, "r:*") as stream:
            members = stream.getmembers()
            if len(members) > 100000 or sum(member.size for member in members) > 4 * 1024**3:
                raise ValueError("Original archive exceeds bounded expansion")
            originals = [member for member in members if row["path"].endswith("/" + member.name.removeprefix("./"))]
            if len(originals) != 1 or not originals[0].isfile() or originals[0].size != 0:
                raise ValueError("Empty notice is not an exact original ordinary archive member")
            for sibling in siblings:
                original = safe_member(components, sibling["path"])
                if not 0 < sibling['size'] <= 4 * 1024**2 or not original.is_file() or original.stat().st_size != sibling["size"] or digest_file(original) != sibling["sha256"] or not original.read_text().strip():
                    raise ValueError("Nonempty original legal text changed")
                matches = [member for member in members if sibling['path'].endswith('/' + member.name.removeprefix('./'))]
                if len(matches) != 1 or not matches[0].isfile() or matches[0].size != sibling['size']:
                    raise ValueError("Sibling grant lacks exact original archive member")
                with stream.extractfile(matches[0]) as content:
                    if content.read(sibling['size'] + 1) != original.read_bytes():
                        raise ValueError("Sibling grant differs from original archive bytes")
        changed.append(row)
    # All planned changes are proven before the one atomic metadata write.
    for row in changed:
        row["role"] = "support"
    if changed:
        atomic_write_json(metadata, specification, mode=0o644)
    return dict(schema_version=1, architecture=specification.get("architecture"),
        original_specification_sha256=before, normalized_specification_sha256=digest_file(metadata),
        reclassified_empty_original_members=[row["path"] for row in changed],
        payload_bytes_changed=0, redistribution_review_complete=False,
        scope="Exact original empty placeholders retained as support, not license grants")
