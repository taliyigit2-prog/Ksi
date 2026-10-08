import ast
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('native_checks_runner', ROOT / 'scripts/run_native_packaged_checks.py')
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class PackagedChecksRunnerTests(unittest.TestCase):
    def test_packaged_environment_removes_interpreter_plugin_and_cache_injection(self):
        with patch.dict(os.environ, dict(PYTHONHOME='/foreign', PYTHONPATH='/foreign',
                         DYLD_LIBRARY_PATH='/foreign', KSI_WORKSPACE_SELECTION='/foreign',
                         VIRTUAL_ENV='/foreign', NUMBA_CACHE_DIR='/foreign',
                         ORT_DISABLE_TELEMETRY='0',
                         QT_PLUGIN_PATH='/foreign', QML2_IMPORT_PATH='/foreign',
                         QT_QPA_PLATFORM_PLUGIN_PATH='/foreign'), clear=True):
            env = RUNNER.packaged_environment(Path('/bundle'), Path('/state'))
        self.assertEqual(env['PATH'], '/usr/bin:/bin:/usr/sbin:/sbin')
        self.assertEqual(env['PYTHONPATH'], '/bundle/runtime/src')
        self.assertEqual(env['KSI_BUNDLE_ROOT'], '/bundle')
        self.assertEqual(env['KSI_STATE_DIRECTORY'], '/state')
        self.assertEqual(env['ORT_DISABLE_TELEMETRY'], '1')
        for key in ('PYTHONHOME', 'DYLD_LIBRARY_PATH', 'KSI_WORKSPACE_SELECTION',
                    'VIRTUAL_ENV', 'NUMBA_CACHE_DIR', 'QT_PLUGIN_PATH',
                    'QML2_IMPORT_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH'):
            self.assertNotIn(key, env)

    def test_linked_or_foreign_app_rejected_before_creating_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            destination = root / 'evidence'
            with patch.object(RUNNER, 'require_native_build_process'):
                with self.assertRaises(ValueError):
                    RUNNER.run(root / 'Foreign.app', destination)
            self.assertFalse(destination.exists())

    def test_rosetta_rejected_before_touching_paths(self):
        with patch.object(RUNNER, 'require_native_build_process', side_effect=ValueError('translated')), \
             patch.object(RUNNER, 'validate_internal_path') as validate:
            with self.assertRaises(ValueError):
                RUNNER.run(Path('/unused'), Path('/unused'))
        validate.assert_not_called()

    def test_every_fixture_compiles_without_loading_native_dependencies(self):
        paths = sorted((ROOT / 'scripts/native_checks').glob('*.py'))
        self.assertEqual(len(paths), 7)
        for path in paths:
            with self.subTest(fixture=path.name):
                ast.parse(path.read_text(encoding='utf-8'), filename=path.name)

    def test_all_public_native_fixture_sources_are_export_allowlisted(self):
        from ksi_local.release_prep import PUBLIC_SCRIPTS
        # The export is intentionally explicit: adding a fixture must not leave
        # CI's acceptance machinery missing from the published source archive.
        for path in (ROOT / 'scripts/native_checks').glob('*.py'):
            self.assertIn(path.relative_to(ROOT).as_posix(), PUBLIC_SCRIPTS)
