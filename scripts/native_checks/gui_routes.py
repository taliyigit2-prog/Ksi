"""Unmocked packaged GUI diagnostic; one part of native acceptance."""
import json
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from ksi_local.atomic_files import atomic_write_json
from ksi_local.bundle_runtime import bundle_root, digest_file, host_architecture
from ksi_local.gui import MainWindow
from ksi_local.internal_storage import validate_internal_path
from ksi_local.native_processor import is_rosetta_translated
from ksi_local.preferences import load_preferences

state = Path(os.environ["KSI_STATE_DIRECTORY"])
if state.exists() or state.is_symlink():
    raise FileExistsError("Packaged GUI diagnostic needs a new isolated state directory")
resources = bundle_root()
if resources is None:
    raise RuntimeError("This diagnostic must use the actual packaged interpreter")
import ksi_local
if not Path(ksi_local.__file__).resolve().is_relative_to(resources / "runtime/src"):
    raise RuntimeError("Diagnostic must import exact packaged application source")
provenance = json.loads((resources / "build-provenance.json").read_text())
output = Path(sys.argv[1]).resolve()
app = QApplication([])
window = MainWindow()
started = time.monotonic()
window.show()
deadline = started + 240
while window.workspace is None and time.monotonic() < deadline:
    app.processEvents()
    time.sleep(0.025)
if window.workspace is None:
    raise RuntimeError("Actual packaged first-run internal workspace did not become ready")
workspace = validate_internal_path(window.workspace.root)
if not workspace.is_relative_to(state.resolve()):
    raise RuntimeError("The diagnostic attempted to use non-isolated application data")
window.workspace_timer.stop()
window.source.setText("synthetic-reference.txt")
routes = []
for route, index in window.studio_shell.routes.items():
    window.studio_shell.buttons[index].click()
    app.processEvents()
    if not window.studio_shell.buttons[index].isChecked():
        raise RuntimeError("Sidebar selection failed: " + route)
    if route in {"download", "video", "document"}:
        if window.workflow_page.mode != route:
            raise RuntimeError("Actual workflow routing failed: " + route)
    elif window.tabs.currentIndex() != index:
        raise RuntimeError("Actual page routing failed: " + route)
    if window.source.text() != "synthetic-reference.txt":
        raise RuntimeError("Sidebar navigation lost form state")
    routes.append(route)
window.theme.setCurrentIndex(window.theme.findData("dark"))
app.processEvents()
if load_preferences().theme != "dark":
    raise RuntimeError("Actual packaged preference did not persist")
window.studio_shell.buttons[window.studio_shell.routes["settings"]].click()
app.processEvents()
if not window.grab().save(str(output.with_suffix(".png"))):
    raise RuntimeError("Actual packaged settings screenshot could not be saved")
startup_seconds = time.monotonic() - started
window.close()
QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
if isValid(window):
    raise RuntimeError("Actual packaged window did not dispose after closing")
atomic_write_json(output, {
    "schema_version": 1,
    "scope": "Unmocked packaged native GUI diagnostic; NOT final model/DMG acceptance",
    "source_commit": provenance["source_commit"],
    "architecture": host_architecture(),
    "rosetta_translated": is_rosetta_translated(),
    "offline_manifest_sha256": digest_file(resources / "offline-manifest.json"),
    "routes": routes,
    "navigation_preserves_state": True,
    "internal_isolated_workspace_ready": True,
    "theme_persisted": True,
    "window_disposed": True,
    "seconds": startup_seconds,
}, mode=0o600)
print(json.dumps({"routes": len(routes), "seconds": startup_seconds, "native_gui_diagnostic": "passed"}))
