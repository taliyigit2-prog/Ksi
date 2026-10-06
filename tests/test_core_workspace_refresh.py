import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ksi_local.core_service import CoreService


class CoreWorkspaceRefreshTests(unittest.TestCase):
    def test_idle_client_rejects_obsolete_workspace_without_touching_store(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = MagicMock()
            core = CoreService(store=store, workspace=SimpleNamespace(root=root / "old"))
            selected = SimpleNamespace(workspace_root=str(root / "new"))
            with patch("ksi_local.workspace_management.load_selection", return_value=selected), patch("ksi_local.settings.resolve_workspace") as resolver:
                with self.assertRaises(RuntimeError):
                    core._validate_workspace()
                resolver.assert_not_called()
            self.assertEqual(store.mock_calls, [])

    def test_current_selection_still_validates_marker_and_volume(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = SimpleNamespace(root=root)
            core = CoreService(store=MagicMock(), workspace=workspace)
            with patch("ksi_local.workspace_management.load_selection", return_value=SimpleNamespace(workspace_root=str(root))), patch("ksi_local.settings.resolve_workspace", return_value=workspace) as resolver:
                core._validate_workspace()
                resolver.assert_called_once_with(initialize=False)
            with patch("ksi_local.workspace_management.load_selection", return_value=SimpleNamespace(workspace_root=str(root))), patch("ksi_local.settings.resolve_workspace", side_effect=RuntimeError("missing volume")):
                with self.assertRaises(RuntimeError):
                    core._validate_workspace()
