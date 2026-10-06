import copy
import unittest

from ksi_local.native_packages import validate_native_lock


class NativePackageLockTests(unittest.TestCase):
    def payload(self):
        filename = "example-1.0-h123_0.conda"
        return {"schema_version": 1, "architecture": "arm64", "packages": [{"name": "example", "filename": filename, "url": "https://conda.anaconda.org/conda-forge/osx-arm64/" + filename, "sha256": "a" * 64, "md5": "b" * 32, "size": 100, "license": "MIT"}]}

    def test_exact_target_and_noarch_inputs(self):
        data = self.payload()
        self.assertEqual(len(validate_native_lock(data)), 1)
        data["packages"][0]["url"] = data["packages"][0]["url"].replace("osx-arm64", "noarch")
        self.assertEqual(len(validate_native_lock(data)), 1)

    def test_wrong_architecture_or_channel_rejected(self):
        for substitution in ("osx-64", "linux-aarch64", "noarch/../osx-arm64"):
            data = self.payload()
            data["packages"][0]["url"] = data["packages"][0]["url"].replace("osx-arm64", substitution)
            with self.subTest(substitution=substitution), self.assertRaises(ValueError):
                validate_native_lock(data)
        data = self.payload()
        data["packages"][0]["url"] = data["packages"][0]["url"].replace("conda-forge", "another-channel")
        with self.assertRaises(ValueError):
            validate_native_lock(data)

    def test_url_encoded_epoch_matches_exact_filename(self):
        data = self.payload()
        row = data["packages"][0]
        row["filename"] = "example-1!1.0-h123_0.conda"
        row["url"] = "https://conda.anaconda.org/conda-forge/osx-arm64/example-1%211.0-h123_0.conda"
        self.assertEqual(len(validate_native_lock(data)), 1)

    def test_unexpected_copyleft_scope_not_silently_admitted(self):
        data = self.payload()
        data["packages"][0]["license"] = "AGPL-3.0-only"
        with self.assertRaises(ValueError):
            validate_native_lock(data)

    def test_duplicate_and_missing_hash_rejected(self):
        data = self.payload()
        data["packages"].append(copy.deepcopy(data["packages"][0]))
        with self.assertRaises(ValueError):
            validate_native_lock(data)
        data = self.payload()
        data["packages"][0]["sha256"] = ""
        with self.assertRaises(ValueError):
            validate_native_lock(data)
