import copy
import unittest

from ksi_local.offline_build import _require_auxiliary_license_metadata


class AuxiliaryLicenseMetadataTests(unittest.TestCase):
    def test_ocr_cannot_claim_mit_or_an_unrelated_original_notice(self):
        for row in ({"license": "MIT", "license_file": "ksi-project-license"},
                    {"license": "Apache-2.0", "license_file": "other-license"}):
            with self.subTest(row=row), self.assertRaises(ValueError):
                _require_auxiliary_license_metadata({("tool", "ocr-helper"): row})

    def test_frozen_downloader_cannot_use_source_only_unlicense_or_missing_aggregate(self):
        for row in ({"license": "Unlicense"}, {"license": "GPL-3.0-or-later", "license_file": "source-unlicense"},
                    {"license": "GPL-3.0-or-later", "license_file": "aggregate", "corresponding_source": "wrong-source"}):
            records = {("tool", "yt-dlp"): row,
                       ("license", "aggregate"): {"path": "licenses/tools/yt-dlp/THIRD_PARTY_LICENSES.txt"}}
            with self.subTest(row=row), self.assertRaises(ValueError):
                _require_auxiliary_license_metadata(records)

    def test_canonical_original_bindings_are_checked_without_rewriting_metadata(self):
        records = {("tool", "ocr-helper"): {"license": "Apache-2.0", "license_file": "ksi-project-license"},
                   ("tool", "yt-dlp"): {"license": "GPL-3.0-or-later", "license_file": "aggregate", "corresponding_source": "yt-dlp-source"},
                   ("license", "aggregate"): {"path": "licenses/tools/yt-dlp/THIRD_PARTY_LICENSES.txt"},
                   ("tool", "other-engine"): {"license": "MIT"}}
        before = copy.deepcopy(records)
        _require_auxiliary_license_metadata(records)
        self.assertEqual(records, before)


if __name__ == "__main__":
    unittest.main()
