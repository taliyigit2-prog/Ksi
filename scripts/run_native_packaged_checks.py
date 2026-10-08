#!/usr/bin/env python3
"""Run real packaged native references; never claim privacy/legal release approval.

All fixtures use owned synthetic data, fresh internal state, OS-only PATH and
the exact sealed application's interpreter. No user installation is replaced.
The result is a partial evidence receipt, not an autonomous_release override.
"""

import argparse
import json
import os
import platform
import subprocess
import tempfile
from pathlib import Path

from ksi_local.atomic_files import atomic_write_json
from ksi_local.bundle_runtime import digest_file
from ksi_local.distribution_integrity import application_digest
from ksi_local.internal_storage import validate_internal_path
from ksi_local.native_processor import require_native_build_process


def packaged_environment(resources, state):
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(('PYTHON', 'DYLD_', 'KSI_', 'VIRTUAL_ENV'))
                   and key not in {'NUMBA_CACHE_DIR', 'QT_PLUGIN_PATH', 'QML2_IMPORT_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH'}}
    environment.update(PATH='/usr/bin:/bin:/usr/sbin:/sbin',
                       PYTHONPATH=str(resources / 'runtime/src'),
                       PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1',
                       QT_QPA_PLATFORM='offscreen', HF_HUB_OFFLINE='1',
                       TRANSFORMERS_OFFLINE='1', HF_DATASETS_OFFLINE='1',
                       ORT_DISABLE_TELEMETRY='1',
                       KSI_BUNDLE_ROOT=str(resources), KSI_STATE_DIRECTORY=str(state))
    return environment


def run(application, destination):
    require_native_build_process()
    application = validate_internal_path(application)
    destination = validate_internal_path(destination)
    if application.name != 'KSI Local Studio.app' or application.is_symlink():
        raise ValueError('An ordinary sealed application is required')
    if destination.exists() or destination.is_symlink():
        raise FileExistsError('Evidence destination must be new')
    resources = application / 'Contents/Resources'
    provenance = json.loads((resources / 'build-provenance.json').read_bytes())
    if platform.system() != 'Darwin' or provenance['architecture'] != platform.machine():
        raise ValueError('Actual native architecture does not match the package')
    destination.mkdir(parents=True, mode=0o700)
    # mkdtemp deliberately retains diagnostics on failure. Never reuse a user's
    # state directory, or delete previous evidence while retrying.
    isolation = Path(tempfile.mkdtemp(prefix='ksi-native-checks-', dir='/private/tmp'))
    validate_internal_path(isolation)
    environment = packaged_environment(resources, isolation / 'state')
    interpreter = resources / 'runtime/python/bin/python3.12'
    fixtures = Path(__file__).resolve().parent / 'native_checks'
    before = application_digest(application)
    results = []

    def invoke(label, fixture, arguments, state=None):
        env = dict(environment)
        if state is not None:
            env['KSI_STATE_DIRECTORY'] = str(state)
        with (destination / (label + '.log')).open('xb') as log:
            subprocess.run([str(interpreter), '-B', str(fixtures / fixture), *map(str, arguments)],
                           env=env, stdin=subprocess.DEVNULL, stdout=log,
                           stderr=subprocess.STDOUT, check=True, timeout=1800)
        results.append(dict(name=label, exit_code=0,
                            log_sha256=digest_file(destination / (label + '.log'))))
        print(json.dumps(dict(completed=label)), flush=True)

    invoke('gui-routes', 'gui_routes.py', [destination / 'gui-routes.json'])
    invoke('gui-controls', 'gui_controls.py', [destination / 'gui-controls.json'])
    invoke('media-image-ocr', 'media_image.py', [destination / 'media-image-ocr'])
    invoke('gui-stop', 'gui_stop.py', [destination / 'gui-stop'])
    invoke('lifecycle', 'lifecycle.py', [destination / 'lifecycle.json'], isolation / 'lifecycle')
    invoke('summary', 'summary.py', [destination / 'summary.json'])
    for mode in ('background', 'translation', 'tts'):
        invoke(mode, 'ai_references.py', [mode, destination / mode])
    if application_digest(application) != before:
        raise ValueError('Actual operation mutated the sealed application')
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(application)],
                   check=True, capture_output=True, timeout=900)
    result = dict(schema_version=1, source_commit=provenance['source_commit'],
                  architecture=platform.machine(), native_process=True,
                  rosetta_translated=False,
                  offline_manifest_sha256=digest_file(resources / 'offline-manifest.json'),
                  application_tree_sha256=before, processes=results,
                  checks=dict(all_processes_succeeded=True, whole_app_unchanged=True,
                              strict_deep_signature_after_models=True),
                  final_product_acceptance=False)
    atomic_write_json(destination / 'packaged-checks.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('application', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.application, args.destination)))
