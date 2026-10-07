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
    def test_explicit_database_cannot_create_external_state(self):
        from ksi_local.job_store import JobStore
        with self.assertRaises(RuntimeError):
            JobStore("/Volumes/unmounted-fixture/KSI-State/jobs.sqlite3")

    def test_explicit_database_preserves_symlink_target(self):
        from ksi_local.job_store import JobStore
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            (base / "original").mkdir()
            (base / "link").symlink_to(base / "original", target_is_directory=True)
            with self.assertRaises(RuntimeError):
                JobStore(base / "link/state/jobs.sqlite3")
            self.assertFalse((base / "original/state").exists())

    def test_explicit_database_requires_absolute_internal_path(self):
        from ksi_local.job_store import JobStore
        with self.assertRaises(RuntimeError):
            JobStore("relative-fixture/jobs.sqlite3")

    def test_database_environment_cannot_reintroduce_external_disk(self):
        from ksi_local.job_store import default_database_path
        with patch.dict(os.environ, {"KSI_STATE_DIRECTORY": "/Volumes/unmounted-fixture/KSI-State"}):
            with self.assertRaises(RuntimeError):
                default_database_path()

    def test_relative_database_environment_is_rejected(self):
        from ksi_local.job_store import default_database_path
        with patch.dict(os.environ, {"KSI_STATE_DIRECTORY": "relative-state-fixture"}):
            with self.assertRaises(RuntimeError):
                default_database_path()

    def test_database_state_link_cannot_redirect_writes(self):
        from ksi_local.job_store import default_database_path
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            (base / "original").mkdir()
            (base / "link").symlink_to(base / "original", target_is_directory=True)
            with patch.dict(os.environ, {"KSI_STATE_DIRECTORY": str(base / "link" / "state")}):
                with self.assertRaises(RuntimeError):
                    default_database_path()
            self.assertFalse((base / "original/state").exists())

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
