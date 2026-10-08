from __future__ import annotations

import json
import fcntl
import os
import plistlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QLabel

from ksi_local import __version__
from ksi_local import gui as gui_module
from ksi_local.gui import FirstRunWizard, MainWindow
from ksi_local.preferences import (
    ONBOARDING_VERSION,
    UserPreferences,
    load_preferences,
    save_preferences,
)


ROOT = Path(__file__).resolve().parents[1]
TEST_APP = QApplication.instance() or QApplication([])


def ready_report(*, passed: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        passed=passed,
        workspace="/internal-fixture/KSI-Workspace",
        workspace_free_bytes=75 * 1024**3,
        tool_integrity_ok=True,
        health=SimpleNamespace(youtube_js_ready=True),
        models=(SimpleNamespace(ready=True),) * 4,
        app_bundle=SimpleNamespace(
            exists=True,
            signed=True,
            native_arm64_only=True,
            native_architecture_matches_host=True,
        ),
    )


class PhaseNineteenFirstRunTests(unittest.TestCase):
    def test_old_preferences_migrate_and_onboarding_completion_persists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "preferences.json"
            target.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "export_kind": "summary",
                        "icloud_warning_acknowledged": True,
                    }
                ),
                encoding="utf-8",
            )
            old = load_preferences(target)
            self.assertEqual(old.onboarding_version, 0)
            completed = UserPreferences(
                export_kind=old.export_kind,
                icloud_warning_acknowledged=old.icloud_warning_acknowledged,
                onboarding_version=ONBOARDING_VERSION,
            )
            save_preferences(completed, target)
            payload = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], 7)
            self.assertEqual(load_preferences(target), completed)

    def test_first_run_wizard_is_local_simple_and_accessible(self) -> None:
        dialog = FirstRunWizard(ready_report())
        labels = "\n".join(label.text() for label in dialog.findChildren(QLabel))
        self.assertIn("KSI Local Studio kullanıma hazır", labels)
        self.assertIn("Dahili disk", labels)
        self.assertNotIn("Harici SSD", labels)
        self.assertIn("FFmpeg, Ollama, yt-dlp ve Deno", labels)
        self.assertIn("Yapay zekâ modeli çalıştırılmadı", labels)
        self.assertIn("Codex/ChatGPT gerekmez", labels)
        buttons = dialog.findChild(QDialogButtonBox)
        assert buttons is not None
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.assertEqual(ok.text(), "KSI Local Studio'yi Kullan")
        self.assertTrue(ok.accessibleName())
        dialog.close()

    def test_accepting_first_run_marks_it_complete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            with (
                patch.dict(os.environ, {"KSI_STATE_DIRECTORY": str(state)}),
                patch.object(MainWindow, "_initialize_workspace", return_value=None),
                patch.object(
                    FirstRunWizard,
                    "exec",
                    return_value=QDialog.DialogCode.Accepted,
                ),
            ):
                window = MainWindow()
                window.health_dialog_mode = "first_run"
                window.maintenance_pending = True
                window._system_status_finished(ready_report(), None)
                self.assertFalse(window.first_run_pending)
                self.assertEqual(
                    window.preferences.onboarding_version, ONBOARDING_VERSION
                )
                self.assertEqual(
                    load_preferences().onboarding_version, ONBOARDING_VERSION
                )
                window.close()

    def test_workspace_poll_cannot_open_a_second_first_run_dialog(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.dict(
                    os.environ,
                    {"KSI_STATE_DIRECTORY": str(Path(directory) / "state")},
                ),
                patch.object(MainWindow, "_initialize_workspace", return_value=None),
            ):
                window = MainWindow()
                window.workspace = SimpleNamespace()
                window.first_run_pending = True
                window.first_run_dialog_active = True
                with patch.object(window, "_show_system_status") as show_status:
                    window._maybe_start_first_run_check()
                show_status.assert_not_called()
                window.close()


class PhaseNineteenPackagingTests(unittest.TestCase):
    def test_second_launch_exits_cleanly_while_first_instance_holds_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            state.mkdir()
            lock = (state / "app.lock").open("a+", encoding="utf-8")
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                with (
                    patch.dict(
                        os.environ, {"KSI_STATE_DIRECTORY": str(state)}
                    ),
                    patch.object(gui_module, "QApplication") as application,
                ):
                    self.assertEqual(gui_module.main(), 0)
                    application.assert_not_called()
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
                lock.close()

    def test_release_metadata_and_installer_are_versioned_for_phase_19(self) -> None:
        with (ROOT / "packaging/Info.plist").open("rb") as handle:
            app_plist = plistlib.load(handle)
        with (ROOT / "packaging/KSI-Local-Studio-Installer-Info.plist").open("rb") as handle:
            installer_plist = plistlib.load(handle)
        self.assertEqual(__version__, "2.0.0")
        self.assertEqual(app_plist["CFBundleShortVersionString"], __version__)
        self.assertEqual(app_plist["CFBundleVersion"], "19")
        self.assertEqual(installer_plist["CFBundleShortVersionString"], __version__)
        self.assertEqual(installer_plist["LSArchitecturePriority"], ["arm64"])

    def test_daily_launcher_uses_bundled_runtime_without_personal_path(self) -> None:
        launcher = (ROOT / "packaging/KSI-Local-Studio-portable-launcher").read_text(encoding="utf-8")
        self.assertIn('$KSI_APP_ROOT/Contents/Resources', launcher)
        self.assertIn('$KSI_RESOURCES/runtime', launcher)
        self.assertIn("PYTHONNOUSERSITE=1", launcher)
        self.assertIn("PYTHONDONTWRITEBYTECODE=1", launcher)
        self.assertIn("OLLAMA_NO_CLOUD=true", launcher)
        self.assertNotIn("Desktop/Ceviri", launcher)
        self.assertNotIn("Library/Application Support", launcher)

    def test_dmg_embeds_models_and_uses_standard_applications_install(self) -> None:
        builder = (ROOT / "src/ksi_local/dmg_transport.py").read_text(encoding="utf-8")
        self.assertIn('entry.role == "model"', builder)
        self.assertIn('symlink_to("/Applications")', builder)
        self.assertIn('"models_included": True', builder)
        for retired in ("scripts/build_personal_dmg.sh", "scripts/install_macos_app.sh",
                        "packaging/KSI-Local-Studio-installer", "packaging/KSI-Local-Studio-launcher"):
            self.assertFalse((ROOT / retired).exists())

    def test_builder_cannot_overwrite_an_existing_application(self) -> None:
        assembler = (ROOT / "src/ksi_local/app_assembly.py").read_text(encoding="utf-8")
        self.assertIn("destination.exists()", assembler)
        self.assertIn("raise FileExistsError", assembler)
        self.assertNotIn("rmtree", assembler)

    def test_developer_assembly_requires_clean_locked_native_inputs(self) -> None:
        assembler = (ROOT / "src/ksi_local/app_assembly.py").read_text(encoding="utf-8")
        self.assertIn("require_native_build_process()", assembler)
        self.assertIn("validate_wheel_lock(wheel_lock)", assembler)
        self.assertIn('provenance.get("wheel_lock_sha256") != lock_digest', assembler)
        self.assertIn("seal_offline_payload(resources, spec)", assembler)
        self.assertIn('"--verify", "--deep", "--strict"', assembler)

    def test_official_frozen_pilot_embeds_front_end_document_dependencies(self) -> None:
        spec = (ROOT / "packaging/pysidedeploy.spec").read_text(encoding="utf-8")
        pilot = (ROOT / "packaging/phase19_pilot.py").read_text(encoding="utf-8")
        self.assertIn("Nuitka==4.1.1", spec)
        self.assertIn("--include-package=docx", spec)
        self.assertIn("--include-package=pypdf", spec)
        self.assertIn("--include-package=lingua", spec)
        self.assertIn("--phase19-self-test", pilot)


if __name__ == "__main__":
    unittest.main()
