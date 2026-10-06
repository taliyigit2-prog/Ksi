from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ksi_local.workspace_management import (
    ApplicationLocation,
    WorkspaceLocation,
    WorkspaceSelection,
    load_selection,
    relocate_workspace,
    save_selection,
)


class Phase26Tests(unittest.TestCase):
    def test_selection_keeps_application_and_workspace_decisions_independent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "selection.json"
            selection = WorkspaceSelection(
                ApplicationLocation.USER_APPLICATIONS,
                WorkspaceLocation.EXTERNAL,
                "/Volumes/Test/KSI-Workspace",
                "workspace-test",
                "volume-test",
            )
            save_selection(selection, path)
            self.assertEqual(load_selection(path), selection)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_external_selection_requires_uuid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                save_selection(WorkspaceSelection(
                    ApplicationLocation.SYSTEM_APPLICATIONS,
                    WorkspaceLocation.EXTERNAL,
                    "/Volumes/Test/KSI-Workspace",
                    "id",
                ), Path(directory) / "selection.json")

    def test_relocation_is_hash_verified_and_preserves_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "old" / "KSI-Workspace"
            destination = root / "new" / "KSI-Workspace"
            (source / "jobs/job-1").mkdir(parents=True)
            (source / "jobs/job-1/source.bin").write_bytes(b"source-data")
            result = relocate_workspace(source, destination)
            self.assertTrue(source.is_dir())
            self.assertEqual((destination / "jobs/job-1/source.bin").read_bytes(), b"source-data")
            receipt = json.loads((destination / ".relocation-receipt.json").read_text())
            self.assertTrue(receipt["source_preserved"])
            self.assertEqual(result.file_count, 1)

    def test_relocation_rejects_symlinks_and_existing_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "item").write_text("x")
            destination = root / "destination"
            destination.mkdir()
            with self.assertRaises(FileExistsError):
                relocate_workspace(source, destination)
            destination.rmdir()
            (source / "link").symlink_to(source / "item")
            with self.assertRaises(ValueError):
                relocate_workspace(source, destination)

    def test_relocation_rejects_preexisting_symlink_staging_area(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            destination = root / "target" / "KSI-Workspace"
            outside = root / "outside"
            source.mkdir()
            outside.mkdir()
            (source / "item.txt").write_text("source")
            destination.parent.mkdir()
            (destination.parent / ".ksi-relocation-KSI-Workspace").symlink_to(
                outside, target_is_directory=True
            )
            with self.assertRaises(ValueError):
                relocate_workspace(source, destination)
            self.assertFalse((outside / "item.txt").exists())


if __name__ == "__main__":
    unittest.main()
