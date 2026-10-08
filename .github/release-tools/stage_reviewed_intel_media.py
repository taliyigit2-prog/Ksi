"""Store only tested/privacy-reviewed Intel media in an unpublished draft.

Never publish, overwrite differing assets, upload an app/source workspace, or
send raw diagnostics. Re-entry skips only independently checksum-matching assets.
"""
import argparse
import json
import io
import importlib.util
import re
import subprocess
from pathlib import Path
from ksi_local.bundle_runtime import digest_file, safe_member


def validated_files(media, privacy_file):
    transport = json.loads((media / 'transport.json').read_bytes())
    privacy = json.loads(privacy_file.read_bytes())
    if (transport['architecture'] != 'x86_64' or privacy['architecture'] != 'x86_64'
            or not re.fullmatch(r'[0-9a-f]{40}', transport['source_commit'])
            or privacy['source_commit'] != transport['source_commit']
            or privacy['offline_manifest_sha256'] != transport['offline_manifest_sha256']
            or privacy['transport_sha256'] != digest_file(media / 'transport.json')
            or privacy['unresolved_findings'] != [] or not privacy['checks']
            or not all(value is True for value in privacy['checks'].values())):
        raise ValueError('Exact privacy-reviewed Intel media required')
    files, names = [], set()
    for row in transport['files']:
        name = row['filename']
        if (Path(name).name != name or name in names
                or not name.startswith('KSI-Local-Studio-2.0.0-x86_64')
                or Path(name).suffix not in {'.dmg', '.dmgpart'}):
            raise ValueError('Invalid media part inventory')
        path = safe_member(media, name)
        if (path.is_symlink() or not 0 < row['size'] < 2 * 1024**3
                or path.stat().st_size != row['size'] or digest_file(path) != row['sha256']):
            raise ValueError('Media part checksum mismatch')
        names.add(name)
        files.append(path)
    if not files or len(files) > 100 or sum(path.suffix == '.dmg' for path in files) != 1:
        raise ValueError('Invalid primary/segment inventory')
    checksums = media / 'SHA256SUMS.txt'
    expected_checksums = ''.join(row['sha256'] + '  ' + row['filename'] + '\n' for row in transport['files'])
    if checksums.read_text(encoding='utf-8') != expected_checksums:
        raise ValueError('Checksum sidecar differs from exact media inventory')
    files.extend([media / 'transport.json', checksums, media / 'Kurulum.txt', privacy_file])
    for path in files:
        if not path.is_file() or path.is_symlink():
            raise ValueError('Upload member must be a regular file')
    spec = importlib.util.spec_from_file_location('sidecar_scanner', Path(__file__).resolve().parents[2] / 'scripts/audit_github_artifacts.py')
    scanner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scanner)
    findings = []
    for path in files[len(transport['files']):]:
        if path.stat().st_size > 16 * 1024**2:
            raise ValueError('Upload sidecar is oversized')
        scanner.scan_stream(io.BytesIO(path.read_bytes()), path.name, findings)
    if findings:
        raise ValueError('Upload sidecar privacy finding')
    return transport, files


def stage(media, privacy_file, repository, tag):
    if repository != 'taliyigit2-prog/Ksi':
        raise ValueError('Draft authorization is repository-specific')
    transport, files = validated_files(media, privacy_file)
    expected_tag = 'ksi-final-intel-' + transport['source_commit'][:7] + '-staging'
    if tag != expected_tag:
        raise ValueError('Draft tag must bind exact approved product source')
    command = ['gh', 'api', 'repos/' + repository + '/releases', '--paginate', '--slurp']
    releases = [release for page in json.loads(subprocess.check_output(command, timeout=120)) for release in page]
    matches = [release for release in releases if release['tag_name'] == tag]
    if not matches:
        subprocess.run(['gh', 'release', 'create', tag, '--repo', repository, '--draft',
                        '--target', transport['source_commit'], '--title', 'KSI Intel reviewed media staging',
                        '--notes', 'Unpublished installation-media checkpoint after native tests and privacy review. Legal/two-architecture public release gates remain pending.'],
                       check=True, timeout=120)
        releases = [release for page in json.loads(subprocess.check_output(command, timeout=120)) for release in page]
        matches = [release for release in releases if release['tag_name'] == tag]
    if len(matches) != 1 or matches[0]['draft'] is not True or matches[0]['target_commitish'] != transport['source_commit']:
        raise ValueError('Existing release is not the authorized unpublished draft')
    # The tag endpoint returns published releases only. Drafts have no public
    # tag yet: use the authenticated listing's immutable release ID instead.
    release_endpoint = 'repos/' + repository + '/releases/' + str(matches[0]['id'])
    for path in files:
        # Re-read before every write: never write to a draft somebody published.
        release = json.loads(subprocess.check_output(['gh', 'api', release_endpoint], timeout=120))
        if release['draft'] is not True:
            raise ValueError('Draft was published; stopping uploads')
        previous = [asset for asset in release['assets'] if asset['name'] == path.name]
        checksum = digest_file(path)
        if previous:
            if (len(previous) != 1 or previous[0]['size'] != path.stat().st_size
                    or previous[0].get('digest') != 'sha256:' + checksum):
                raise ValueError('Existing draft asset differs; not overwritten')
        else:
            subprocess.run(['gh', 'release', 'upload', tag, str(path), '--repo', repository],
                           check=True, timeout=3600)
            release = json.loads(subprocess.check_output(['gh', 'api', release_endpoint], timeout=120))
            current = [asset for asset in release['assets'] if asset['name'] == path.name]
            if (release['draft'] is not True or len(current) != 1 or current[0]['size'] != path.stat().st_size
                    or current[0].get('digest') != 'sha256:' + checksum):
                raise ValueError('Remote uploaded asset failed size/digest/draft verification')
        print(json.dumps(dict(asset=path.name, sha256=checksum, unpublished=True)), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('media', type=Path)
    parser.add_argument('privacy_file', type=Path)
    parser.add_argument('repository')
    parser.add_argument('tag')
    args = parser.parse_args()
    stage(args.media.absolute(), args.privacy_file.absolute(), args.repository, args.tag)
