import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from ksi_local.internal_storage import validate_internal_path
from ksi_local.settings import resolve_workspace
from ksi_local.workspace_management import default_internal_workspace, load_selection
from ksi_local.workspace_selection import change_workspace


class InternalStorageTests(unittest.TestCase):
    def test_external_path_is_rejected_even_when_disk_is_missing(self):
        with self.assertRaises(RuntimeError):
            validate_internal_path(Path("/Volumes/unmounted-fixture/KSI-Workspace"))

    def test_external_selection_is_preserved_before_internal_initialization(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            state = base / "state"
            state.mkdir()
            original = {"schema_version": 1, "application_location": "user_applications",
                        "workspace_location": "external", "workspace_root": "/Volumes/unmounted-fixture/KSI-Workspace",
                        "workspace_id": str(uuid.uuid4()), "volume_uuid": "volume-123"}
            (state / "workspace-selection.json").write_text(json.dumps(original))
            with patch.dict(os.environ, {"KSI_STATE_DIRECTORY": str(state)}), patch("ksi_local.storage.discover_mounted_volumes", side_effect=AssertionError("No external discovery")):
                paths = resolve_workspace(initialize=True)
                self.assertEqual(paths.root, default_internal_workspace())
                self.assertEqual(load_selection().workspace_location.value, "internal")
                self.assertEqual(json.loads((state / "legacy-external-workspace.local.json").read_text()), original)
                self.assertEqual(resolve_workspace().root, paths.root)

    def test_interrupted_first_setup_reuses_existing_marker(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary).resolve() / "state"
            with patch.dict(os.environ, {"KSI_STATE_DIRECTORY": str(state)}):
                target = default_internal_workspace()
                target.mkdir(parents=True)
                identity = str(uuid.uuid4())
                (target / ".workspace-id").write_text(json.dumps({"workspace_id": identity}))
                (target / "keep.txt").write_text("original")
                paths = resolve_workspace(initialize=True)
                self.assertEqual(load_selection().workspace_id, identity)
                self.assertEqual((paths.root / "keep.txt").read_text(), "original")

    def test_symlink_cannot_redirect_internal_choice(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            (base / "other").mkdir()
            (base / "link").symlink_to(base / "other", target_is_directory=True)
            with self.assertRaises(RuntimeError):
                validate_internal_path(base / "link" / "workspace")

    def test_selection_api_does_not_discover_external_disks(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            with patch.dict(os.environ, {"KSI_STATE_DIRECTORY": str(base / "state")}):
                self.assertEqual(change_workspace(base / "work").workspace_location.value, "internal")


if __name__ == "__main__":
    unittest.main()
