"""Audit exact sealed app and read-only media before authorized draft storage.

No blanket dependency exemption: each flagged byte must occur in its original
SHA-pinned public wheel/runtime, or in a previously independently verified exact
public source archive. Unknown findings fail closed. This is not legal approval.
"""
import argparse
import collections
import ctypes
import hashlib
import importlib.util
import io
import json
import os
import re
import tarfile
import zipfile
from pathlib import Path

from ksi_local.atomic_files import atomic_write_json
from ksi_local.build_inputs import fetch_pinned_input
from ksi_local.bundle_runtime import digest_file, safe_member
from ksi_local.distribution_integrity import application_digest, mounted_distribution


def load_scanner(repository):
    spec = importlib.util.spec_from_file_location('artifact_scanner', repository / 'scripts/audit_github_artifacts.py')
    scanner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scanner)
    return scanner


def fingerprints(scanner, content, findings):
    result = collections.Counter()
    for finding in findings:
        match = scanner.COMPILED[finding['rule']].match(content, finding['byte_offset'])
        if match is None:
            raise ValueError('Finding no longer matches the inspected bytes')
        result[(finding['rule'], hashlib.sha256(match.group()).hexdigest())] += 1
    return result


def original_matches(scanner, content, findings, original):
    expected = fingerprints(scanner, content, findings)
    actual = collections.Counter()
    for rule in {item['rule'] for item in findings}:
        for match in scanner.COMPILED[rule].finditer(original):
            actual[(rule, hashlib.sha256(match.group()).hexdigest())] += 1
    return not (expected - actual)


def wheel_candidates(member, pins):
    package = member.split('/', 1)[0].split('.dist-info', 1)[0]
    normalized = re.sub(r'[-_.]+', '-', package).lower()
    aliases = {'sklearn': 'scikit-learn', 'pil': 'pillow', 'yaml': 'pyyaml'}
    normalized = aliases.get(normalized, normalized)
    return [pin for pin in pins if normalized == re.sub(r'[-_.]+', '-', pin['name']).lower()
            or normalized.startswith(re.sub(r'[-_.]+', '-', pin['name']).lower() + '-')
            or (normalized == 'pyside6' and pin['name'].lower().startswith('pyside6'))]


def read_attributes(path):
    native = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
    native.listxattr.argtypes = (ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int)
    native.listxattr.restype = ctypes.c_ssize_t
    native.getxattr.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int)
    native.getxattr.restype = ctypes.c_ssize_t
    encoded, flags = os.fsencode(path), 0x0001 | 0x0020
    size = native.listxattr(encoded, None, 0, flags)
    if not 0 <= size <= 1024**2:
        raise OSError(ctypes.get_errno(), 'Read-only attribute listing failed')
    if not size:
        return []
    names = ctypes.create_string_buffer(size)
    if native.listxattr(encoded, names, size, flags) != size or not names.raw.endswith(b'\0'):
        raise ValueError('Attribute names changed')
    results = []
    for name in names.raw[:-1].split(b'\0'):
        length = native.getxattr(encoded, name, None, 0, 0, flags)
        if not 0 <= length <= 16 * 1024**2:
            raise OSError(ctypes.get_errno(), 'Read-only attribute size failed')
        data = ctypes.create_string_buffer(max(1, length))
        if native.getxattr(encoded, name, data, length, 0, flags) != length:
            raise ValueError('Attribute content changed')
        results.append((os.fsdecode(name), data.raw[:length]))
    return results


def audit(repository, app, media, packaged_receipt, installation_receipt, destination, allow_network=False):
    repository, app, media, destination = [path.absolute() for path in (repository, app, media, destination)]
    if destination.exists() or destination.is_symlink():
        raise FileExistsError('Privacy evidence requires a new destination')
    resources = app / 'Contents/Resources'
    provenance = json.loads((resources / 'build-provenance.json').read_bytes())
    manifest = digest_file(resources / 'offline-manifest.json')
    tree = application_digest(app)
    for path in (packaged_receipt, installation_receipt):
        receipt = json.loads(path.read_bytes())
        if (receipt['source_commit'] != provenance['source_commit']
                or receipt['architecture'] != provenance['architecture']
                or receipt['offline_manifest_sha256'] != manifest
                or receipt['application_tree_sha256'] != tree
                or receipt['native_process'] is not True
                or receipt['rosetta_translated'] is not False
                or not receipt['checks'] or not all(value is True for value in receipt['checks'].values())):
            raise ValueError('Exact native packaged/independent-installation acceptance is required')
    scanner = load_scanner(repository)
    findings, count, total = [], 0, 0
    for path in sorted(app.rglob('*')):
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise ValueError('Unexpected app member')
        if path.is_file():
            with path.open('rb') as stream:
                total += scanner.scan_stream(stream, path.relative_to(app).as_posix(), findings)
            count += 1
    destination.mkdir(parents=True, mode=0o700)
    atomic_write_json(destination / 'raw.local.json', dict(potential_findings=findings, files_scanned=count, bytes_scanned=total))
    # Source pins are reviewed exact public tar bytes, not filename exceptions.
    archive_pins = json.loads((Path(__file__).parent / 'public-source-privacy-pins.json').read_bytes())['archives']
    architecture = provenance['architecture']
    locks = {'main': json.loads((repository / ('config/python-wheels-' + architecture + '.json')).read_bytes())['wheels']}
    speech = 'piper' if architecture == 'x86_64' else 'chatterbox'
    locks[speech] = json.loads((repository / ('config/python-' + speech + '-wheels-' + architecture + '.json')).read_bytes())['wheels']
    python_pin = json.loads((repository / 'config/runtime-sources.json').read_bytes())['inputs']['python-' + architecture]
    groups = collections.defaultdict(list)
    for finding in findings:
        groups[finding['member']].append(finding)
    proofs = []
    def fetch(pin):
        cache = destination / 'original-cache' / pin['sha256']
        if not allow_network and not cache.exists():
            raise ValueError('Original verification needs explicitly authorized build-only network access')
        return fetch_pinned_input(pin, cache)
    for member, rows in groups.items():
        content = safe_member(app, member).read_bytes()
        relative = member.removeprefix('Contents/Resources/')
        origin = None
        if relative in archive_pins and digest_file(safe_member(resources, relative)) == archive_pins[relative]['sha256']:
            origin = dict(kind='independently_reviewed_exact_public_source_archive', **archive_pins[relative])
        elif '/site-packages/' in member or '/lib/tcl9/' in member:
            suffix = member.split('/site-packages/', 1)[1] if '/site-packages/' in member else None
            scope = speech if '/engines/' + speech + '/' in member else 'main'
            for pin in wheel_candidates(suffix, locks[scope]) if suffix else []:
                with zipfile.ZipFile(fetch(pin)) as wheel:
                    if suffix not in wheel.namelist():
                        continue
                    if original_matches(scanner, content, rows, wheel.read(suffix)):
                        origin = dict(kind='exact_original_public_wheel', sha256=pin['sha256'], source_url=pin['url'])
                        break
            if origin is None:
                name = 'python/' + member.split('/python/', 1)[1]
                with tarfile.open(fetch(python_pin)) as archive:
                    try:
                        source = archive.extractfile(name)
                    except KeyError:
                        source = None
                    if source is not None:
                        with source:
                            if original_matches(scanner, content, rows, source.read()):
                                origin = dict(kind='exact_original_public_python', sha256=python_pin['sha256'], source_url=python_pin['url'])
        if origin is None:
            raise ValueError('Unresolved packaged privacy finding; draft upload forbidden')
        proofs.append(dict(member=member, findings=len(rows), **origin))
    transport = json.loads((media / 'transport.json').read_bytes())
    for key in ('source_commit', 'architecture'):
        if transport[key] != provenance[key]:
            raise ValueError('Media identity does not match')
    if transport['offline_manifest_sha256'] != manifest:
        raise ValueError('Media manifest does not match')
    for row in transport['files']:
        path = safe_member(media, row['filename'])
        if path.stat().st_size != row['size'] or digest_file(path) != row['sha256']:
            raise ValueError('Media part checksum does not match')
    primary = [row['filename'] for row in transport['files'] if row['filename'].endswith('.dmg')]
    if len(primary) != 1:
        raise ValueError('Exactly one primary DMG required')
    metadata_findings, attributes_count, entries_count = [], 0, 0
    with mounted_distribution(media / primary[0]) as mount:
        embedded = mount / app.name
        if application_digest(embedded) != tree:
            raise ValueError('Read-only media contains different app bytes')
        def walk_error(error):
            raise error
        entries = [mount]
        for directory, folders, files in os.walk(mount, followlinks=False, onerror=walk_error):
            entries.extend(Path(directory) / name for name in folders + files)
        for path in entries:
            entries_count += 1
            name = path.relative_to(mount).as_posix()
            scanner.scan_stream(io.BytesIO(name.encode()), name + ':filename', metadata_findings)
            if path.is_symlink():
                if path != mount / 'Applications' or str(path.readlink()) != '/Applications':
                    raise ValueError('Unexpected media link')
            elif not (path.is_dir() or path.is_file()):
                raise ValueError('Unexpected media special file')
            for attribute, content in read_attributes(path):
                attributes_count += 1
                scanner.scan_stream(io.BytesIO(content), name + ':xattr:' + attribute, metadata_findings)
            if path.is_file() and not path.is_relative_to(embedded):
                with path.open('rb') as stream:
                    scanner.scan_stream(stream, name, metadata_findings)
    if metadata_findings or application_digest(app) != tree:
        raise ValueError('Media metadata finding or app mutation; draft upload forbidden')
    result = dict(schema_version=1, source_commit=provenance['source_commit'], architecture=architecture,
                  offline_manifest_sha256=manifest, application_tree_sha256=tree,
                  transport_sha256=digest_file(media / 'transport.json'), files_scanned=count, bytes_scanned=total,
                  classified_original_members=proofs, filesystem_entries=entries_count, xattr_streams_checked=attributes_count,
                  unresolved_findings=[], checks=dict(complete_app_bytes_scanned=True, every_flagged_byte_public_original_bound=True,
                  readonly_media_exact=True, all_media_filenames_and_xattrs_scanned=True, zero_unresolved_findings=True),
                  legal_acceptance=False, public_release_approved=False)
    atomic_write_json(destination / 'privacy.json', result)
    print(json.dumps(dict(files_scanned=count, classified_members=len(proofs), unresolved_findings=0)), flush=True)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('repository', 'application', 'media', 'packaged_receipt', 'installation_receipt', 'destination'):
        parser.add_argument(name, type=Path)
    parser.add_argument('--allow-network', action='store_true')
    args = parser.parse_args()
    audit(args.repository, args.application, args.media, args.packaged_receipt, args.installation_receipt, args.destination, args.allow_network)
