"""Real widget signals/preferences/validation; no mock services or user data."""
import json
import sys
import time
from pathlib import Path
from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox
from shiboken6 import isValid
from ksi_local.atomic_files import atomic_write_json
from ksi_local.bundle_runtime import bundle_root, digest_file, host_architecture
from ksi_local.gui import MainWindow
from ksi_local.native_processor import is_rosetta_translated
from ksi_local.preferences import load_preferences

resources = bundle_root()
if resources is None:
    raise RuntimeError("Requires actual packaged interpreter")
import ksi_local
if not Path(ksi_local.__file__).resolve().is_relative_to(resources / "runtime/src"):
    raise RuntimeError("Diagnostic must import exact packaged application source")
app = QApplication([])
window = MainWindow()
window.show()
deadline = time.monotonic() + 240
while (window.workspace is None or window.maintenance_pending) and time.monotonic() < deadline:
    app.processEvents()
    time.sleep(0.02)
if window.workspace is None or window.maintenance_pending:
    raise RuntimeError("Packaged workspace not ready")
window.workspace_timer.stop()
window.studio_shell.buttons[window.studio_shell.routes["video"]].click()
window.source.clear()
old_jobs = [record.id for record in window.store.list_jobs()]
locales, themes, dialogs = [], [], []
timer = QTimer()
def respond_to_actual_warning():
    for widget in app.topLevelWidgets():
        if isinstance(widget, QMessageBox) and widget.isVisible():
            dialogs.append(widget.text())
            widget.accept()
timer.timeout.connect(respond_to_actual_warning)
timer.start(20)
for index in range(window.ui_language.count()):
    window.ui_language.setCurrentIndex(index)
    app.processEvents()
    code = window.ui_language.currentData()
    if load_preferences().ui_language != code:
        raise RuntimeError("Actual language signal did not persist")
    for theme_index in range(window.theme.count()):
        window.theme.setCurrentIndex(theme_index)
        app.processEvents()
        if load_preferences().theme != window.theme.currentData():
            raise RuntimeError("Actual theme signal did not persist")
        themes.append([code, window.theme.currentData()])
    count = len(dialogs)
    window.start_button.click()
    if len(dialogs) != count + 1 or not dialogs[-1].strip():
        raise RuntimeError("Start button did not dispatch real missing-source validation")
    locales.append(code)
timer.stop()
if [record.id for record in window.store.list_jobs()] != old_jobs:
    raise RuntimeError("Empty form incorrectly created a job")
window.ui_language.setCurrentIndex(window.ui_language.findData("tr"))
window.theme.setCurrentIndex(window.theme.findData("dark"))
window.system_full_verify_button.click()
if not window.health_status_pending or window.health_thread is None:
    raise RuntimeError("Actual verification button did not dispatch")
health = window.health_thread
started = time.monotonic()
window.close()
QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
close_seconds = time.monotonic() - started
disposed = not isValid(window)
health.join(timeout=180)
app.processEvents()
checks = dict(language_signals_persist=len(locales) == 8,
    all_language_theme_pairs=len(themes) == 24, real_form_validation=len(dialogs) == 8,
    no_job_on_invalid_form=True, actual_verification_button_dispatch=True,
    closes_during_readonly_verification=disposed and close_seconds < 1,
    late_health_completion_safe=not health.is_alive())
atomic_write_json(Path(sys.argv[1]), dict(scope="Actual native packaged GUI controls; not full distribution/cancellation acceptance",
    source_commit=json.loads((resources / "build-provenance.json").read_bytes())["source_commit"],
    offline_manifest_sha256=digest_file(resources / "offline-manifest.json"), architecture=host_architecture(),
    rosetta_translated=is_rosetta_translated(), checks=checks, locales=locales,
    preference_pairs=themes, close_seconds=close_seconds))
if not all(checks.values()):
    raise RuntimeError("Packaged GUI control checks failed")
print(json.dumps(dict(checks=checks, close_seconds=close_seconds)))
