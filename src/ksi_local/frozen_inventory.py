"""Bounded PyInstaller inventory without executing or extracting bundled code."""

import hashlib
import re
import struct
import zlib
from pathlib import Path

from ksi_local.bundle_runtime import digest_file


COOKIE_MAGIC = b"MEI\014\013\012\013\016"
COOKIE = struct.Struct("!8sIIII64s")
ENTRY = struct.Struct("!IIIIBB")
MAX_MEMBER = 64 * 1024**2


def _data_table(data):
    """Read only primitive marshal TOC types, never code objects or globals.

    PyInstaller stores its PYZ table as marshal, not pickle. A narrow parser
    avoids unmarshalling arbitrary Python code and bounds references/depth.
    """
    if not 0 < len(data) <= 4 * 1024**2:
        raise ValueError("Frozen Python table exceeds its size bound")
    position, nodes, references = 0, 0, []

    def take(size):
        nonlocal position
        if not 0 <= size <= len(data) - position:
            raise ValueError("Frozen Python table is truncated")
        value = data[position:position + size]
        position += size
        return value

    def integer():
        return struct.unpack("<i", take(4))[0]

    def read(depth=0):
        nonlocal nodes
        nodes += 1
        if depth > 16 or nodes > 1000000:
            raise ValueError("Frozen Python table exceeds its structural bound")
        tag = take(1)[0]
        referenced = bool(tag & 128)
        tag = chr(tag & 127)
        slot = len(references) if referenced else None
        if referenced:
            references.append(None)
        if tag == "i":
            value = integer()
        elif tag in {"z", "Z", "a", "A", "u", "t"}:
            length = take(1)[0] if tag in {"z", "Z"} else integer()
            value = take(length).decode("utf-8")
        elif tag in {"[", "(", ")"}:
            length = take(1)[0] if tag == ")" else integer()
            if not 0 <= length <= 100000:
                raise ValueError("Frozen Python table has excessive container entries")
            values = [read(depth + 1) for _ in range(length)]
            value = values if tag == "[" else tuple(values)
        elif tag == "r" and not referenced:
            index = integer()
            if not 0 <= index < len(references) or references[index] is None:
                raise ValueError("Frozen Python table has an invalid or cyclic reference")
            value = references[index]
        else:
            raise ValueError("Frozen Python table cannot contain code, globals or unsupported types")
        if slot is not None:
            references[slot] = value
        return value

    result = read()
    if position != len(data):
        raise ValueError("Frozen Python table contains trailing data")
    return result


def _inflate(data, expected=None):
    inflater = zlib.decompressobj()
    result = inflater.decompress(data, MAX_MEMBER + 1)
    if len(result) > MAX_MEMBER or inflater.unconsumed_tail or not inflater.eof or inflater.unused_data:
        raise ValueError("Frozen archive member exceeds its bound or is malformed")
    if expected is not None and len(result) != expected:
        raise ValueError("Frozen archive member has an incorrect expanded size")
    return result


def inspect_frozen_archive(executable: Path, expected_sha256: str) -> dict:
    if (executable.is_symlink() or not executable.is_file()
            or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
            or not 0 < executable.stat().st_size <= 256 * 1024**2
            or digest_file(executable) != expected_sha256):
        raise ValueError("Frozen inventory requires an exact pinned ordinary executable")
    data = executable.read_bytes()
    cookie_offset = data.rfind(COOKIE_MAGIC)
    if cookie_offset < 0 or cookie_offset + COOKIE.size > len(data):
        raise ValueError("Frozen archive cookie is missing")
    _, length, toc_offset, toc_size, python_abi, library = COOKIE.unpack_from(data, cookie_offset)
    base = cookie_offset + COOKIE.size - length
    toc_start, toc_end = base + toc_offset, base + toc_offset + toc_size
    if base < 0 or toc_start < base or toc_end > cookie_offset or not 0 < toc_size <= 4 * 1024**2:
        raise ValueError("Frozen archive table lies outside its package")
    members, names, selected = [], set(), {}
    cursor = toc_start
    while cursor < toc_end:
        if len(members) >= 100000 or cursor + ENTRY.size > toc_end:
            raise ValueError("Frozen archive table is excessive or truncated")
        row_length, offset, compressed, expanded, flag, kind = ENTRY.unpack_from(data, cursor)
        if row_length <= ENTRY.size or cursor + row_length > toc_end or flag not in {0, 1}:
            raise ValueError("Frozen archive entry is malformed")
        raw_name = data[cursor + ENTRY.size:cursor + row_length]
        if b"\0" not in raw_name:
            raise ValueError("Frozen archive name lacks its terminator")
        name = raw_name.rstrip(b"\0").decode("utf-8")
        if not name or name in names or b"\0" in name.encode() or "\\" in name or name.startswith("/") or ".." in name.split("/"):
            raise ValueError("Frozen archive name is unsafe or duplicated")
        if (offset > toc_offset or compressed > toc_offset - offset
                or expanded > MAX_MEMBER):
            raise ValueError("Frozen archive entry exceeds its package or expansion bound")
        raw = data[base + offset:base + offset + compressed]
        row = dict(path=name, kind=chr(kind), stored_size=compressed, expanded_size=expanded,
                   stored_sha256=hashlib.sha256(raw).hexdigest())
        members.append(row)
        names.add(name)
        if kind == ord("z") or name.endswith(".dist-info/METADATA"):
            selected[name] = _inflate(raw, expanded) if flag else raw
        cursor += row_length
    modules, packages = [], []
    for name, content in selected.items():
        if name.endswith(".dist-info/METADATA"):
            fields = {}
            for line in content.decode("utf-8").splitlines():
                if line.startswith(("Name: ", "Version: ", "License-Expression: ")):
                    key, value = line.split(": ", 1)
                    fields[key] = value
            if not fields.get("Name") or not fields.get("Version"):
                raise ValueError("Frozen package metadata is incomplete")
            packages.append(dict(path=name, metadata_sha256=hashlib.sha256(content).hexdigest(), **fields))
        else:
            if len(content) < 12 or content[:4] != b"PYZ\0":
                raise ValueError("Frozen Python module container is malformed")
            offset = struct.unpack_from("!I", content, 8)[0]
            if not 12 <= offset < len(content):
                raise ValueError("Frozen Python module table is outside its container")
            records = _data_table(content[offset:])
            if not isinstance(records, (list, dict)) or len(records) > 100000:
                raise ValueError("Frozen Python module inventory is malformed")
            pairs = records.items() if isinstance(records, dict) else records
            for module, record in pairs:
                if (not isinstance(module, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", module)
                        or not isinstance(record, tuple) or len(record) != 3
                        or not all(type(value) is int for value in record)):
                    raise ValueError("Frozen Python module record is malformed")
                kind, start, length = record
                if not 12 <= start <= offset or not 0 <= length <= offset - start:
                    raise ValueError("Frozen Python module lies outside its container")
                modules.append(dict(module=module, kind=kind, size=length,
                                    sha256=hashlib.sha256(content[start:start + length]).hexdigest()))
    if not modules:
        raise ValueError("Frozen executable has no inspectable Python module inventory")
    return dict(schema_version=1, executable_sha256=expected_sha256, python_abi=python_abi,
                members=members, modules=modules, distribution_metadata=packages,
                dependency_versions_complete=False, redistribution_review_complete=False)
