#!/usr/bin/env python3
"""Independent internal installation from verified, read-only native DMG.

Does not replace /Applications, delete user data or promote legal/privacy gates.
"""

import argparse
import json
import platform
import subprocess
import tempfile
from pathlib import Path

from ksi_local.atomic_files import atomic_write_json
from ksi_local.bundle_runtime import digest_file, safe_member
from ksi_local.distribution_integrity import application_digest, mounted_distribution
from ksi_local.internal_storage import validate_internal_path
from ksi_local.native_processor import require_native_build_process
from run_native_packaged_checks import packaged_environment


def verify(application, transport_root, destination):
    require_native_build_process()
    application = validate_internal_path(application)
    transport_root = validate_internal_path(transport_root)
    destination = validate_internal_path(destination)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError('Installation diagnostic requires a new destination')
    resources = application / 'Contents/Resources'
    provenance = json.loads((resources / 'build-provenance.json').read_bytes())
    transport = json.loads((transport_root / 'transport.json').read_bytes())
    if platform.system() != 'Darwin' or platform.machine() != provenance['architecture']:
        raise ValueError('Native processor does not match the actual application')
    for key in ('source_commit', 'architecture'):
        if transport[key] != provenance[key]:
            raise ValueError('Installation media does not match source/architecture')
    if transport['offline_manifest_sha256'] != digest_file(resources / 'offline-manifest.json'):
        raise ValueError('Installation media manifest mismatch')
    files = transport.get('files')
    if not isinstance(files, list) or not 1 <= len(files) <= 100:
        raise ValueError('Invalid installation part inventory')
    names = set()
    for item in files:
        filename = item['filename']
        if Path(filename).name != filename or filename in names:
            raise ValueError('Unsafe or duplicate part name')
        path = safe_member(transport_root, filename)
        if (path.suffix not in {'.dmg', '.dmgpart'} or path.is_symlink()
                or not 0 < item['size'] < 2 * 1024**3
                or path.stat().st_size != item['size'] or digest_file(path) != item['sha256']):
            raise ValueError('Installation segment integrity failed')
        names.add(filename)
    primary = [name for name in names if name.endswith('.dmg')]
    if len(primary) != 1:
        raise ValueError('Exactly one primary DMG required')
    expected = application_digest(application)
    destination.mkdir(parents=True, mode=0o700)
    installed = destination / application.name
    with mounted_distribution(transport_root / primary[0]) as mount:
        embedded = mount / application.name
        if application_digest(embedded) != expected:
            raise ValueError('Embedded app differs from accepted build')
        shortcut = mount / 'Applications'
        if not shortcut.is_symlink() or str(shortcut.readlink()) != '/Applications':
            raise ValueError('Missing standard installation shortcut')
        if 'Apple' not in (mount / 'Kurulum.txt').read_text(encoding='utf-8'):
            raise ValueError('First-opening warning is missing')
        subprocess.run(['/usr/bin/ditto', str(embedded), str(installed)], check=True, timeout=1800)
    if application_digest(installed) != expected:
        raise ValueError('Independent installation copy differs')
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(installed)],
                   check=True, capture_output=True, timeout=900)
    isolated = Path(tempfile.mkdtemp(prefix='ksi-install-checks-', dir='/private/tmp'))
    validate_internal_path(isolated)
    resources = installed / 'Contents/Resources'
    environment = packaged_environment(resources, isolated / 'state')
    interpreter = resources / 'runtime/python/bin/python3.12'
    fixtures = Path(__file__).resolve().parent / 'native_checks'
    for name, fixture, arguments in (
            ('first-offline-gui', 'gui_routes.py', [destination / 'installed-gui.json']),
            ('installed-speech', 'ai_references.py', ['tts', destination / 'installed-speech'])):
        with (destination / (name + '.log')).open('xb') as log:
            subprocess.run([str(interpreter), '-B', str(fixtures / fixture), *map(str, arguments)],
                           env=environment, stdin=subprocess.DEVNULL, stdout=log,
                           stderr=subprocess.STDOUT, check=True, timeout=1800)
    environment.update(KSI_ACCEPTANCE_MODE='1', KSI_LIFECYCLE_CHECK_MS='10000')
    with (destination / 'ordinary-launcher.log').open('xb') as log:
        subprocess.run([str(installed / 'Contents/MacOS/KSI-Local-Studio')], env=environment,
                       stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                       check=True, timeout=180)
    if application_digest(installed) != expected:
        raise ValueError('Installed operations modified the app')
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(installed)],
                   check=True, capture_output=True, timeout=900)
    result = dict(schema_version=1, source_commit=provenance['source_commit'],
                  architecture=platform.machine(), native_process=True, rosetta_translated=False,
                  offline_manifest_sha256=transport['offline_manifest_sha256'],
                  application_tree_sha256=expected, checks=dict(
                      dmg_segments_verified=True, readonly_embedded_app_exact=True,
                      standard_installation_shortcut=True, installation_copy_exact=True,
                      initial_offline_gui_models_ready=True, installed_tts_asr_reference=True,
                      ordinary_launcher_restart_success=True, installed_app_unchanged=True,
                      strict_deep_signature_after_models=True, isolated_internal_storage=True,
                      os_only_tool_path=True), final_product_acceptance=False)
    atomic_write_json(destination / 'offline-install.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('application', type=Path)
    parser.add_argument('transport', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.application, args.transport, args.destination)))
