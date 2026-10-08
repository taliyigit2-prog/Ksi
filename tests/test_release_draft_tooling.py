"""Release tooling checks do not change or execute the accepted product."""
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / '.github/release-tools'
def module(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result

AUDIT = module('audit_installation_privacy')
STAGE = module('stage_reviewed_intel_media')

class ReviewedDraftToolingTests(unittest.TestCase):
    def test_original_fingerprint_must_match_every_occurrence(self):
        scanner = AUDIT.load_scanner(ROOT)
        content = b'https://' + b'synthetic:fixture' + b'@example.invalid/'
        findings = []
        scanner.scan_stream(io.BytesIO(content), 'fixture', findings)
        self.assertTrue(AUDIT.original_matches(scanner, content, findings, content))
        self.assertFalse(AUDIT.original_matches(scanner, content, findings, b'no original match'))
        with self.assertRaises(ValueError):
            AUDIT.original_matches(scanner, b'changed', findings, content)

    def test_same_filename_does_not_exempt_changed_original_bytes(self):
        pins = json.loads((TOOLS / 'public-source-privacy-pins.json').read_bytes())['archives']
        self.assertEqual(len(pins), 8)
        for name, pin in pins.items():
            self.assertTrue(name.endswith('/corresponding-source.tar'))
            self.assertRegex(pin['sha256'], r'^[0-9a-f]{64}$')
            self.assertRegex(pin['source_commit'], r'^[0-9a-f]{40}$')
            self.assertTrue(pin['source_url'].startswith('https://github.com/'))

    def test_wheel_candidates_account_for_original_import_spelling(self):
        pins = [dict(name='scikit-learn'), dict(name='unrelated'), dict(name='PySide6_Essentials')]
        self.assertEqual(AUDIT.wheel_candidates('sklearn/utils/a.py', pins), pins[:1])
        self.assertEqual(AUDIT.wheel_candidates('PySide6/Qt/file', pins), pins[2:])
        self.assertEqual(AUDIT.wheel_candidates('unknown/module.py', pins), [])

    def test_no_gh_action_outside_authorized_repository(self):
        with patch.object(STAGE.subprocess, 'run') as run, patch.object(STAGE.subprocess, 'check_output') as read:
            with self.assertRaises(ValueError):
                STAGE.stage(Path('/unused'), Path('/unused'), 'foreign/repo', 'foreign')
        run.assert_not_called()
        read.assert_not_called()

    def test_unreviewed_media_cannot_be_staged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'transport.json').write_text(json.dumps(dict(architecture='x86_64', source_commit='a'*40,
                offline_manifest_sha256='b'*64, files=[])))
            privacy = root / 'privacy.json'
            privacy.write_text(json.dumps(dict(architecture='x86_64', source_commit='a'*40,
                offline_manifest_sha256='b'*64, transport_sha256='c'*64, unresolved_findings=[], checks={})))
            with patch.object(STAGE.subprocess, 'run') as run, patch.object(STAGE.subprocess, 'check_output') as read:
                with self.assertRaises(ValueError):
                    STAGE.stage(root, privacy, 'taliyigit2-prog/Ksi', 'ksi-final-intel-aaaaaaa-staging')
            run.assert_not_called()
            read.assert_not_called()

    def test_tooling_workflow_never_implicitly_publishes(self):
        source = (ROOT / '.github/workflows/intel-candidate.yml').read_text()
        self.assertIn('ref: ${{ inputs.product_commit }}', source)
        self.assertIn('ref: ${{ github.sha }}', source)
        self.assertIn('if: inputs.stage_reviewed_media', source)
        self.assertNotIn('gh release edit', source)
        self.assertIn("'--draft'", (TOOLS / 'stage_reviewed_intel_media.py').read_text())
