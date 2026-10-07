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
            self.assertEqual(payload["schema_version"], 6)
            self.assertEqual(load_preferences(target), completed)

    def test_first_run_wizard_is_local_simple_and_accessible(self) -> None:
        dialog = FirstRunWizard(ready_report())
        labels = "\n".join(label.text() for label in dialog.findChildren(QLabel))
        self.assertIn("KSI Local Studio kullanıma hazır", labels)
        self.assertIn("Harici SSD", labels)
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
        self.assertEqual(__version__, "2.0.0.dev0")
        self.assertEqual(app_plist["CFBundleShortVersionString"], __version__)
        self.assertEqual(app_plist["CFBundleVersion"], "19")
        self.assertEqual(installer_plist["CFBundleShortVersionString"], __version__)
        self.assertEqual(installer_plist["LSArchitecturePriority"], ["arm64"])

    def test_daily_launcher_uses_private_runtime_without_project_path(self) -> None:
        launcher = (ROOT / "packaging/KSI-Local-Studio-launcher").read_text(encoding="utf-8")
        self.assertIn("Library/Application Support/KSI Local Studio/runtime", launcher)
        self.assertIn("PYTHONNOUSERSITE=1", launcher)
        self.assertIn("PYTHONDONTWRITEBYTECODE=1", launcher)
        self.assertIn("OLLAMA_NO_CLOUD=true", launcher)
        self.assertNotIn("Desktop/Ceviri", launcher)

    def test_dmg_contains_one_click_installer_and_keeps_models_on_ssd(self) -> None:
        builder = (ROOT / "scripts/build_personal_dmg.sh").read_text(encoding="utf-8")
        installer = (ROOT / "packaging/KSI-Local-Studio-installer").read_text(encoding="utf-8")
        self.assertIn("KSI Local Studio Kur.app", builder)
        self.assertIn(".payload/runtime", builder)
        self.assertIn("Desktop/KSI Local Studio/1 - Programı Aç.app", installer)
        self.assertIn("Önceki KSI Local Studio sürümü korundu", installer)
        self.assertIn("Büyük yapay zekâ modelleri harici SSD üzerinde kaldı", installer)
        self.assertNotIn("models/ollama", builder)
        self.assertNotIn("models/whisper", builder)
        self.assertGreaterEqual(builder.count("PYTHONDONTWRITEBYTECODE=1"), 4)

    def test_installers_refuse_to_swap_a_running_application(self) -> None:
        personal_installer = (ROOT / "packaging/KSI-Local-Studio-installer").read_text(
            encoding="utf-8"
        )
        developer_installer = (ROOT / "scripts/install_macos_app.sh").read_text(
            encoding="utf-8"
        )
        for script in (personal_installer, developer_installer):
            with self.subTest(script=script[:40]):
                self.assertIn("app.lock", script)
                self.assertIn("LOCK_EX | fcntl.LOCK_NB", script)
                self.assertIn("local rollback_status=$?", script)
                self.assertNotIn("local status=$?", script)
                self.assertGreaterEqual(script.count("PYTHONDONTWRITEBYTECODE=1"), 4)

    def test_developer_installer_stages_and_audits_portable_python(self) -> None:
        installer = (ROOT / "scripts/install_macos_app.sh").read_text(encoding="utf-8")
        self.assertIn("KSI_PORTABLE_PYTHON_ROOT", installer)
        self.assertIn("cpython-3.12.13-macos-aarch64-none", installer)
        self.assertIn('"$STAGED_RUNTIME/python"', installer)
        self.assertIn("prepare", installer)
        self.assertIn("runtime_portability", installer)
        self.assertIn(
            'PYTHONDONTWRITEBYTECODE=1 "$STAGED_RUNTIME/venv/bin/python"',
            installer,
        )
        self.assertIn(
            'PYTHONDONTWRITEBYTECODE=1 "$STAGED_RUNTIME/python/bin/python3.12"',
            installer,
        )
        self.assertLess(
            installer.index("runtime_portability"),
            installer.index("RUNTIME_SWAPPED=true"),
        )

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
