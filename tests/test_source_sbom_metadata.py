import tempfile
import unittest
from pathlib import Path

from ksi_local.release_prep import _spdx_document


class SourceSbomMetadataTests(unittest.TestCase):
    def test_version_comes_from_exact_staged_project_not_builder_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for version in ("2.0.0", "3.2.1.dev7"):
                (root / "pyproject.toml").write_text(
                    '[project]\nname = "ksi-local-studio"\nversion = "' + version + '"\n')
                sbom = _spdx_document(root, [])
                self.assertEqual(sbom["packages"][0]["versionInfo"], version)
                self.assertIn("Source-tree SBOM", sbom["annotations"][0]["comment"])

    def test_missing_invalid_and_foreign_project_metadata_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(ValueError):
                _spdx_document(root, [])
            for text in ('[project', '[other]\nname = "ksi-local-studio"\n',
                         '[project]\nname = "other-product"\nversion = "2.0.0"\n',
                         '[project]\nname = "ksi-local-studio"\nversion = 2\n',
                         '[project]\nname = "ksi-local-studio"\nversion = "stale"\n'):
                with self.subTest(text=text):
                    (root / "pyproject.toml").write_text(text)
                    with self.assertRaises(ValueError):
                        _spdx_document(root, [])

    def test_linked_and_oversized_metadata_are_not_read(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "original.toml"
            original.write_text('[project]\nname = "ksi-local-studio"\nversion = "2.0.0"\n')
            metadata = root / "pyproject.toml"
            metadata.symlink_to(original)
            with self.assertRaises(ValueError):
                _spdx_document(root, [])
            metadata.unlink()
            metadata.write_text("#" * 65537)
            with self.assertRaises(ValueError):
                _spdx_document(root, [])


if __name__ == "__main__":
    unittest.main()
