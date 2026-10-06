import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from ksi_local.workspace_access import active_workspace_descriptor, workspace_access
from ksi_local.workspace_management import load_selection
from ksi_local.workspace_selection import change_workspace


class WorkspaceSelectionTests(unittest.TestCase):
    def test_explicit_new_location_preserves_previous_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / "previous"
            source.mkdir()
            (source / "keep.txt").write_text("original")
            target = root / "new"
            with patch.dict(os.environ, {"KSI_STATE_DIRECTORY": str(root / "state"), "KSI_WORKSPACE_SELECTION_FILE": str(root / "selection.json")}):
                selected = change_workspace(target, volumes=[])
                self.assertEqual(load_selection(), selected)
                self.assertEqual((source / "keep.txt").read_text(), "original")
                self.assertFalse((target / "keep.txt").exists())
                self.assertEqual(json.loads((target / ".workspace-id").read_text())["workspace_id"], selected.workspace_id)

    def test_unrelated_populated_folder_is_never_adopted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            target = root / "private-folder"
            target.mkdir()
            (target / "keep.txt").write_text("original")
            with patch.dict(os.environ, {"KSI_STATE_DIRECTORY": str(root / "state"), "KSI_WORKSPACE_SELECTION_FILE": str(root / "selection.json")}):
                with self.assertRaises(ValueError):
                    change_workspace(target, volumes=[])
                self.assertFalse((root / "selection.json").exists())
                self.assertEqual((target / "keep.txt").read_text(), "original")

    def test_missing_external_volume_cannot_be_created_as_internal_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(os.environ, {"KSI_STATE_DIRECTORY": str(Path(temporary).resolve() / "state")}):
                with self.assertRaises(RuntimeError):
                    change_workspace(Path("/Volumes/KSI-unmounted-test/KSI-Workspace"), volumes=[])

    def test_adopts_existing_identity_without_rewriting_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            target = root / "existing"
            target.mkdir()
            identity = str(uuid.uuid4())
            (target / ".workspace-id").write_text(json.dumps({"workspace_id": identity}))
            before = (target / ".workspace-id").read_bytes()
            with patch.dict(os.environ, {"KSI_STATE_DIRECTORY": str(root / "state"), "KSI_WORKSPACE_SELECTION_FILE": str(root / "selection.json")}):
                self.assertEqual(change_workspace(target, volumes=[]).workspace_id, identity)
            self.assertEqual((target / ".workspace-id").read_bytes(), before)

    def test_reader_excludes_mutation_and_releases_on_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            with patch.dict(os.environ, {"KSI_STATE_DIRECTORY": str(root / "state")}):
                with self.assertRaisesRegex(ValueError, "fixture"):
                    with workspace_access():
                        self.assertIsNotNone(active_workspace_descriptor())
                        with self.assertRaises(RuntimeError):
                            with workspace_access(mutation=True):
                                pass
                        raise ValueError("fixture")
                self.assertIsNone(active_workspace_descriptor())
                with workspace_access(mutation=True):
                    with self.assertRaises(RuntimeError):
                        with workspace_access():
                            pass

    def test_corrupt_saved_selection_never_silently_falls_back(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "selection.json"
            target.write_text("{invalid")
            with self.assertRaises(RuntimeError):
                load_selection(target)
            self.assertEqual(target.read_text(), "{invalid")
