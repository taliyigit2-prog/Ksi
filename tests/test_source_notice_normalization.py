import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from ksi_local.source_notice_normalization import normalize_empty_source_notices


class SourceNoticeNormalizationTests(unittest.TestCase):
    def fixture(self, root):
        archive = root / 'sources/example.tar'
        archive.parent.mkdir()
        with tarfile.open(archive, 'w') as stream:
            member = tarfile.TarInfo('example/build/LICENSE')
            member.size = 0
            stream.addfile(member, io.BytesIO(b''))
            data = b'Synthetic legal text fixture, not a grant'
            member = tarfile.TarInfo('example/LICENSE')
            member.size = len(data)
            stream.addfile(member, io.BytesIO(data))
        pin = hashlib.sha256(archive.read_bytes()).hexdigest()
        url = 'https://example.org/original.tar'
        rows = []
        for name, data, role, identifier in (
            ('licenses/example/build/LICENSE', b'', 'license', 'empty-original'),
            ('licenses/example/LICENSE', b'Synthetic legal text fixture, not a grant', 'license', 'root-original')):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            rows.append(dict(path=name, sha256=hashlib.sha256(data).hexdigest(), size=len(data),
                role=role, identifier=identifier, source_url=url, revision=pin))
        rows.append(dict(path='sources/example.tar', sha256=pin, size=archive.stat().st_size,
            role='support', identifier='source-original', source_url=url, revision=pin))
        metadata = root / 'component-specification.json'
        metadata.write_text(json.dumps(dict(schema_version=1, architecture='arm64', files=rows, models=[])))
        return metadata, rows

    def test_exact_empty_original_is_retained_without_license_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata, rows = self.fixture(root)
            result = normalize_empty_source_notices(root)
            self.assertEqual(result['reclassified_empty_original_members'], [rows[0]['path']])
            self.assertEqual(result['payload_bytes_changed'], 0)
            self.assertFalse(result['redistribution_review_complete'])
            self.assertEqual((root / rows[0]['path']).read_bytes(), b'')
            self.assertEqual(json.loads(metadata.read_bytes())['files'][0]['role'], 'support')
            before = metadata.read_bytes()
            self.assertEqual(normalize_empty_source_notices(root)['reclassified_empty_original_members'], [])
            self.assertEqual(metadata.read_bytes(), before)

    def test_missing_changed_ambiguous_or_used_grants_never_mutate(self):
        for mode in ('archive', 'notice', 'missing-grant', 'used-grant', 'model-notice', 'duplicate-source', 'linked-empty'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                metadata, rows = self.fixture(root)
                document = json.loads(metadata.read_bytes())
                if mode == 'archive':
                    (root / rows[2]['path']).write_bytes(b'changed')
                elif mode == 'notice':
                    (root / rows[1]['path']).write_bytes(b'changed')
                elif mode == 'missing-grant':
                    document['files'].pop(1)
                elif mode == 'used-grant':
                    document['files'][1]['license_file'] = 'empty-original'
                elif mode == 'model-notice':
                    document['models'] = [dict(notices=['empty-original'])]
                elif mode == 'duplicate-source':
                    duplicate = dict(rows[2], path='sources/duplicate.tar', identifier='duplicate')
                    document['files'].append(duplicate)
                else:
                    (root / rows[0]['path']).unlink()
                    (root / rows[0]['path']).symlink_to(root / rows[1]['path'])
                metadata.write_text(json.dumps(document))
                before = metadata.read_bytes()
                with self.assertRaises(ValueError):
                    normalize_empty_source_notices(root)
                self.assertEqual(metadata.read_bytes(), before)

    def test_unrelated_empty_member_cannot_be_reclassified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata, rows = self.fixture(root)
            moved = root / 'licenses/unrelated/LICENSE'
            moved.parent.mkdir()
            (root / rows[0]['path']).rename(moved)
            rows[0]['path'] = moved.relative_to(root).as_posix()
            metadata.write_text(json.dumps(dict(schema_version=1, files=rows)))
            before = metadata.read_bytes()
            with self.assertRaises(ValueError):
                normalize_empty_source_notices(root)
            self.assertEqual(metadata.read_bytes(), before)
