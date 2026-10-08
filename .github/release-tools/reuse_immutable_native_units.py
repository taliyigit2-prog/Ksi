"""Reuse only exact previously passed native source tests, never model/install tests."""
import argparse
import hashlib
import json
import platform
import re
import subprocess
import zipfile
from pathlib import Path
from ksi_local.atomic_files import atomic_write_bytes, atomic_write_json
from ksi_local.bundle_runtime import digest_file
from ksi_local.native_processor import require_native_build_process

PRODUCT='874c8e6585037ca2fbf6bddb11edd55187fa9dd7'
ARTIFACT='cfbabeb0ac4ed72f0fd0a09b9fabb5d6b3ee472bde1acc9271ca66e7adc5f2ca'
TREE='5eb0ab48cc17ea0de5276a3d0afe96e6bd3a9f2d'


def validate_binding(provenance,head,tree,manifest,preflight,original_build,packaged,log):
    if head!=PRODUCT or tree!=TREE:
        raise ValueError('Product source changed; original source tests cannot be reused')
    if (provenance['source_commit']!=PRODUCT or provenance['architecture']!='x86_64'
            or preflight['source_commit']!=PRODUCT or preflight['architecture']!='x86_64'
            or preflight['native_process'] is not True or preflight['rosetta_translated'] is not False
            or original_build['source_commit']!=PRODUCT or original_build['architecture']!='x86_64'
            or packaged['source_commit']!=PRODUCT or packaged['architecture']!='x86_64'
            or packaged['native_process'] is not True or packaged['rosetta_translated'] is not False
            or original_build['manifest_sha256']!=manifest or packaged['offline_manifest_sha256']!=manifest
            or not re.search(rb'Ran 675 tests in [0-9.]+s\s+OK\s*$',log)
            or b'skipped=' in log):
        raise ValueError('Exact source/architecture/SDK-manifest/full-suite success binding failed')


def verify(repository,application,archive,output):
    require_native_build_process()
    if platform.machine()!='x86_64' or platform.system()!='Darwin':
        raise ValueError('Immutable native proof is Intel-specific')
    if output.exists() or output.is_symlink() or archive.is_symlink() or digest_file(archive)!=ARTIFACT:
        raise ValueError('Only the independently audited exact original artifact is permitted')
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repository,text=True).strip()
    tree=subprocess.check_output(['git','rev-parse','HEAD^{tree}'],cwd=repository,text=True).strip()
    resources=application/'Contents/Resources'
    provenance=json.loads((resources/'build-provenance.json').read_bytes())
    manifest=digest_file(resources/'offline-manifest.json')
    with zipfile.ZipFile(archive) as files:
        if files.testzip() is not None:
            raise ValueError('Original artifact ZIP integrity failed')
        preflight=json.loads(files.read('preflight.json'))
        original_build=json.loads(files.read('native-app-build.json'))
        packaged=json.loads(files.read('native-packaged-checks/packaged-checks.json'))
        log=files.read('native-unit-suite.log')
    validate_binding(provenance,head,tree,manifest,preflight,original_build,packaged,log)
    log_path=output.parent/'native-unit-suite.log'
    if log_path.exists():
        raise FileExistsError('Do not overwrite a current unit log')
    atomic_write_bytes(log_path,log,mode=0o400)
    result=dict(schema_version=1,source_commit=PRODUCT,source_tree=TREE,architecture='x86_64',
                native_process=True,rosetta_translated=False,offline_manifest_sha256=manifest,
                tests=675,skipped=0,origin_run_id=37814426381,origin_artifact_sha256=ARTIFACT,
                log_sha256=hashlib.sha256(log).hexdigest(),source_suite_passed_in_origin_run=True,
                origin_whole_workflow_passed=False,models_or_installation_evidence_reused=False,
                scope='Previously genuinely passed immutable native source suite only. Current affected identity tests must also pass. Packaged models and independent installation are rerun, not reused.')
    atomic_write_json(output,result)
    print(json.dumps(dict(immutable_native_source_tests=675,exact_source_tree=True,exact_sdk_manifest=True)),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('repository','application','archive','output'):
        parser.add_argument(name,type=Path)
    args=parser.parse_args()
    verify(args.repository,args.application,args.archive,args.output)
