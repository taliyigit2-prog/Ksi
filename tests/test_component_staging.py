import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from ksi_local.component_staging import merge_components


class ComponentStagingTests(unittest.TestCase):
    def piece(self, root, name, *, content=b"synthetic notice", identifier="notice", architecture="arm64", executable=False):
        piece = root / name
        piece.mkdir()
        path = piece / "licenses/notice.txt"
        path.parent.mkdir()
        path.write_bytes(content)
        path.chmod(0o755 if executable else 0o644)
        row = {"path": "licenses/notice.txt", "identifier": identifier, "role": "license",
            "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
        (piece / "component-specification.json").write_text(json.dumps({"schema_version": 1,
            "architecture": architecture, "files": [row]}))
        return piece

    def test_identical_original_notices_are_deduplicated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first, second = self.piece(root, "first"), self.piece(root, "second")
            destination = root / "merged"
            result = merge_components((first, second), destination, architecture="arm64")
            self.assertEqual(result["files"], 1)
            self.assertFalse(result["acceptance_tested"])
            copied = destination / "licenses/notice.txt"
            self.assertNotEqual(copied.stat().st_ino, (first / "licenses/notice.txt").stat().st_ino)

    def test_different_records_never_overwrite_one_another(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = self.piece(root, "first")
            second = self.piece(root, "second", content=b"different")
            with self.assertRaises(ValueError):
                merge_components((first, second), root / "merged", architecture="arm64")
            self.assertFalse((root / "merged").exists())

    def test_other_architecture_and_changed_input_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            piece = self.piece(root, "wrong-architecture", architecture="x86_64")
            with self.assertRaises(ValueError):
                merge_components((piece,), root / "merged", architecture="arm64")
            piece = self.piece(root, "changed")
            (piece / "licenses/notice.txt").write_bytes(b"tampered")
            with self.assertRaises(ValueError):
                merge_components((piece,), root / "tampered", architecture="arm64")

    def test_existing_destination_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            piece = self.piece(root, "first")
            destination = root / "existing"
            destination.mkdir()
            with self.assertRaises(FileExistsError):
                merge_components((piece,), destination, architecture="arm64")

    def test_symlink_input_is_not_followed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            piece = self.piece(root, "first")
            original = piece / "licenses/notice.txt"
            original.rename(piece / "ordinary.txt")
            original.symlink_to("../ordinary.txt")
            with self.assertRaises(ValueError):
                merge_components((piece,), root / "merged", architecture="arm64")
