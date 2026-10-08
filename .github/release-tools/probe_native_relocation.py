"""Catch real relocation/first-voice failures before expensive repeated media tests."""
import argparse
import importlib.util
import json
import platform
import subprocess
import sys
import tempfile
from pathlib import Path
from ksi_local.atomic_files import atomic_write_json
from ksi_local.bundle_runtime import digest_file
from ksi_local.distribution_integrity import application_digest
from ksi_local.internal_storage import validate_internal_path
from ksi_local.native_processor import require_native_build_process
from ksi_local.privacy import redact_sensitive_text

parser=argparse.ArgumentParser(description=__doc__)
for name in ('repository','application','destination'):
    parser.add_argument(name,type=Path)
args=parser.parse_args()
repository=args.repository.absolute()
application=validate_internal_path(args.application)
destination=validate_internal_path(args.destination)
require_native_build_process()
if (application.parent!=repository/'build/native-app' or application.name!='KSI Local Studio.app'
        or destination!=repository/'build/native-relocation-probe' or destination.exists()):
    raise ValueError('Probe operates only on this freshly generated native build, never an installed user app')
sys.path.insert(0,str(repository/'scripts'))
from run_native_packaged_checks import packaged_environment
before=application_digest(application)
provenance=json.loads((application/'Contents/Resources/build-provenance.json').read_bytes())
assert provenance['architecture']==platform.machine()
destination.mkdir(mode=0o700)
relocated=destination/application.name
application.rename(relocated)
try:
    with tempfile.TemporaryDirectory(prefix='ksi-relocation-models-',dir='/private/tmp') as temporary:
        resources=relocated/'Contents/Resources'
        environment=packaged_environment(resources,Path(temporary)/'state')
        interpreter=resources/'runtime/python/bin/python3.12'
        fixtures=repository/'scripts/native_checks'
        for name,fixture,arguments in (
            ('first-offline-gui','gui_routes.py',[destination/'gui.json']),
            ('installed-speech','ai_references.py',['tts',destination/'speech'])):
            with (destination/(name+'.log')).open('xb') as log:
                subprocess.run([str(interpreter),'-B',str(fixtures/fixture),*map(str,arguments)],
                               env=environment,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
                               check=True,timeout=1800)
    assert application_digest(relocated)==before,'Real relocation/model use mutated the sealed app'
    subprocess.run(['/usr/bin/codesign','--verify','--deep','--strict',str(relocated)],
                   check=True,capture_output=True,timeout=900)
    atomic_write_json(destination/'relocation.json',dict(source_commit=provenance['source_commit'],
        architecture=platform.machine(),native_process=True,rosetta_translated=False,
        offline_manifest_sha256=digest_file(relocated/'Contents/Resources/offline-manifest.json'),
        application_tree_sha256=before,checks=dict(relocated_fresh_gui=True,relocated_first_tts_asr=True,
        original_app_bytes_unchanged=True,strict_deep_signature=True),independent_dmg_installation=False))
except Exception as error:
    spec=importlib.util.spec_from_file_location('artifact_scanner',repository/'scripts/audit_github_artifacts.py')
    scanner=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scanner)
    tails={}
    for path in destination.glob('*.log'):
        with path.open('rb') as stream:
            stream.seek(max(0,path.stat().st_size-16384))
            text=redact_sensitive_text(stream.read().decode('utf-8',errors='replace'))
        data=text.encode()
        for pattern in scanner.COMPILED.values():
            data=pattern.sub(b'[redacted]',data)
        tails[path.name]=data.decode('utf-8',errors='replace')
    atomic_write_json(destination/'failure-diagnostic.json',dict(exception_type=type(error).__name__,
                      synthetic_log_tails=tails,probe_passed=False))
    print(json.dumps(dict(exception_type=type(error).__name__,synthetic_log_tails=tails)),flush=True)
    raise
finally:
    if relocated.exists():
        if application.exists() or application.is_symlink():
            raise RuntimeError('Original path became occupied; not overwritten')
        relocated.rename(application)
