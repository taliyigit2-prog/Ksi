import importlib.util
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('retry_input',ROOT/'.github/release-tools/restore_pinned_models_with_retries.py')
RETRY=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RETRY)


class ReleaseInputRetryTests(unittest.TestCase):
    def test_transient_provider_error_retries_unchanged_pins(self):
        models,ollama=object(),object()
        error=urllib.error.HTTPError('https://example.invalid/public',503,'Unavailable',{},None)
        with patch.object(RETRY,'restore_model_inputs',side_effect=[error,dict(restored_model_files=1)]) as restore, \
             patch.object(RETRY.time,'sleep') as sleep:
            result=RETRY.restore(Path('/owned-build'),models,ollama,allow_network=True)
        self.assertEqual(result['download_attempts'],2)
        self.assertEqual(restore.call_args_list[0],restore.call_args_list[1])
        self.assertEqual(restore.call_args.args,(Path('/owned-build'),models,ollama))
        sleep.assert_called_once_with(10)

    def test_digest_or_existing_file_error_never_retried(self):
        for error in (RuntimeError('Digest mismatch'),FileExistsError('Do not overwrite'),ValueError('Unsafe URL')):
            with patch.object(RETRY,'restore_model_inputs',side_effect=error) as restore, \
                 patch.object(RETRY.time,'sleep') as sleep:
                with self.assertRaises(type(error)):
                    RETRY.restore(Path('/owned-build'),{}, {},allow_network=True)
            self.assertEqual(restore.call_count,1);sleep.assert_not_called()

    def test_permanent_http_error_not_retried(self):
        error=urllib.error.HTTPError('https://example.invalid/public',404,'Missing',{},None)
        with patch.object(RETRY,'restore_model_inputs',side_effect=error) as restore:
            with self.assertRaises(urllib.error.HTTPError):
                RETRY.restore(Path('/owned-build'),{}, {},allow_network=True)
        self.assertEqual(restore.call_count,1)

    def test_retries_bounded_and_never_report_false_success(self):
        with patch.object(RETRY,'restore_model_inputs',side_effect=TimeoutError('Timeout')) as restore, \
             patch.object(RETRY.time,'sleep') as sleep:
            with self.assertRaises(TimeoutError):
                RETRY.restore(Path('/owned-build'),{}, {},allow_network=True)
        self.assertEqual(restore.call_count,4)
        self.assertEqual([row.args[0] for row in sleep.call_args_list],[10,20,30])

    def test_missing_network_authority_does_not_fetch(self):
        with patch.object(RETRY,'restore_model_inputs') as restore:
            with self.assertRaises(ValueError):RETRY.restore(Path('/unused'),{}, {})
        restore.assert_not_called()
