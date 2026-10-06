import copy
import unittest

from ksi_local.wheel_lock import requirements_text, validate_wheel_lock


class WheelLockTests(unittest.TestCase):
    def payload(self):
        filename = "example-1.0-py3-none-any.whl"
        return {"schema_version": 1, "architecture": "arm64", "python": "3.12", "wheels": [{"name": "example", "version": "1.0", "filename": filename, "url": "https://files.pythonhosted.org/packages/" + filename, "size": 100, "sha256": "a" * 64}]}

    def test_requirements_are_exact_and_hashed(self):
        self.assertEqual(requirements_text(self.payload()), "example==1.0 --hash=sha256:" + "a" * 64 + "\n")

    def test_credentials_other_hosts_and_queries_rejected(self):
        for url in ("https://other.example/example.whl", "https://user:password@files.pythonhosted.org/example.whl", self.payload()["wheels"][0]["url"] + "?token=value"):
            with self.subTest(url=url):
                data = self.payload()
                data["wheels"][0]["url"] = url
                with self.assertRaises(ValueError):
                    validate_wheel_lock(data)

    def test_duplicate_canonical_name_rejected(self):
        data = self.payload()
        data["wheels"].append(copy.deepcopy(data["wheels"][0]))
        with self.assertRaises(ValueError):
            validate_wheel_lock(data)

    def test_wrong_architecture_and_newer_macos_rejected(self):
        for platform in ("macosx_14_0_x86_64", "macosx_26_0_arm64", "manylinux_2_17_aarch64"):
            data = self.payload()
            row = data["wheels"][0]
            row["filename"] = f"example-1.0-cp312-cp312-{platform}.whl"
            row["url"] = "https://files.pythonhosted.org/packages/" + row["filename"]
            with self.subTest(platform=platform), self.assertRaises(ValueError):
                validate_wheel_lock(data)

    def test_universal_abi3_supported(self):
        data = self.payload()
        row = data["wheels"][0]
        row["filename"] = "example-1.0-cp310-abi3-macosx_13_0_universal2.whl"
        row["url"] = "https://files.pythonhosted.org/packages/" + row["filename"]
        self.assertEqual(len(validate_wheel_lock(data)), 1)
