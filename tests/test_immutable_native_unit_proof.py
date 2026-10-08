import copy
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('immutable_units',ROOT/'.github/release-tools/reuse_immutable_native_units.py')
PROOF=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROOF)


def fixture():
    identity=dict(source_commit=PROOF.PRODUCT,architecture='x86_64')
    native=dict(identity,native_process=True,rosetta_translated=False)
    manifest='b'*64
    return [identity,PROOF.PRODUCT,PROOF.TREE,manifest,native,
            dict(identity,manifest_sha256=manifest),
            dict(native,offline_manifest_sha256=manifest),b'Ran 675 tests in 123.456s\n\nOK\n']


class ImmutableNativeUnitProofTests(unittest.TestCase):
    def test_exact_binding_is_valid(self):
        PROOF.validate_binding(*fixture())

    def test_changed_source_tree_or_manifest_cannot_reuse(self):
        for index,value in ((1,'a'*40),(2,'a'*40),(3,'c'*64)):
            args=fixture();args[index]=value
            with self.assertRaises(ValueError):
                PROOF.validate_binding(*args)

    def test_failed_skipped_or_different_suite_cannot_reuse(self):
        for log in (b'Ran 675 tests in 1.0s\n\nFAILED (failures=1)\n',
                    b'Ran 675 tests in 1.0s\n\nOK (skipped=1)\n',
                    b'Ran 674 tests in 1.0s\n\nOK\n'):
            args=fixture();args[-1]=log
            with self.assertRaises(ValueError):
                PROOF.validate_binding(*args)

    def test_foreign_or_translated_native_origin_cannot_reuse(self):
        for index,key,value in ((4,'native_process',False),(4,'rosetta_translated',True),
                                (6,'architecture','arm64')):
            args=copy.deepcopy(fixture());args[index][key]=value
            with self.assertRaises(ValueError):
                PROOF.validate_binding(*args)

    def test_processor_rejected_before_file_or_git_access(self):
        with patch.object(PROOF,'require_native_build_process',side_effect=ValueError('translated')), \
             patch.object(PROOF.subprocess,'check_output') as git:
            with self.assertRaises(ValueError):
                PROOF.verify(Path('/unused'),Path('/unused'),Path('/unused'),Path('/unused'))
        git.assert_not_called()
