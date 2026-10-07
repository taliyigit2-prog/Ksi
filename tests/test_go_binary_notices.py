import runpy
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


binary_modules = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/fetch_go_binary_notices.py"))["binary_modules"]


class GoBinaryNoticeTests(unittest.TestCase):
    def inventory(self, output, sums="example.org/public v1.0.0 h1:synthetic\n"):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "go.sum").write_text(sums)
            with patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout=output)) as inspection:
                result = binary_modules(root / "binary", root, "arm64", "pinned-commit")
                self.assertEqual(inspection.call_args.args[0][:3], ["go", "version", "-m"])
                return result

    def test_native_dependencies_are_bound_to_original_checksums(self):
        result = self.inventory("\tdep\texample.org/public\tv1.0.0\th1:synthetic\n\tbuild\tGOARCH=arm64\n\tbuild\tvcs.revision=pinned-commit\n")
        self.assertEqual(result, [("example.org/public", "v1.0.0", "h1:synthetic")])

    def test_other_architecture_revision_checksum_and_replacement_fail_closed(self):
        good = "\tdep\texample.org/public\tv1.0.0\th1:synthetic\n\tbuild\tGOARCH=arm64\n\tbuild\tvcs.revision=pinned-commit\n"
        for invalid in (good.replace("arm64", "amd64"), good.replace("pinned-commit", "other"),
                        good.replace("arm64", "arm64-extra"), good.replace("pinned-commit", "pinned-commit-extra"),
                        good.replace("h1:synthetic", "h1:changed"), good + "\t=>\t/private/local/module\n"):
            with self.subTest(output=invalid), self.assertRaises(ValueError):
                self.inventory(invalid)

    def test_empty_and_duplicate_dependencies_are_rejected(self):
        header = "\tbuild\tGOARCH=arm64\n\tbuild\tvcs.revision=pinned-commit\n"
        dep = "\tdep\texample.org/public\tv1.0.0\th1:synthetic\n"
        for invalid in (header, header + dep + dep):
            with self.assertRaises(ValueError):
                self.inventory(invalid)
