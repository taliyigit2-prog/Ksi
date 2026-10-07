import runpy
import tempfile
import unittest
from pathlib import Path


build_bindings = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/build_model_notice_bindings.py"))["build_bindings"]


class ModelNoticeBindingTests(unittest.TestCase):
    def test_existing_binding_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "bindings.json"
            output.write_text("original private build evidence")
            with self.assertRaises(ValueError):
                build_bindings(root, root, output)
            self.assertEqual(output.read_text(), "original private build evidence")

    def test_linked_build_root_is_not_followed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            linked = root / "linked"
            linked.symlink_to(root, target_is_directory=True)
            with self.assertRaises(ValueError):
                build_bindings(root, linked, root / "bindings.json")

    def test_missing_public_pins_do_not_create_bindings(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(ValueError):
                build_bindings(root, root, root / "bindings.json")
            self.assertFalse((root / "bindings.json").exists())
