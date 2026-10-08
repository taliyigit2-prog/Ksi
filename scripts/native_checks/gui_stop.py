"""Actual form start/stop on a running native media job; synthetic source only."""
import json
import sys
import time
from pathlib import Path
import ksi_local
from PySide6.QtWidgets import QApplication
from ksi_local.atomic_files import atomic_write_json
from ksi_local.bundle_runtime import bundle_root, digest_file, tool_path
from ksi_local.engine_runner import run_engine
from ksi_local.gui import MainWindow
from ksi_local.job_store import JobStatus

resources = bundle_root()
if resources is None or not Path(ksi_local.__file__).resolve().is_relative_to(resources / "runtime/src"):
    raise RuntimeError("Exact packaged source required")
target = Path(sys.argv[1]).absolute()
if target.exists() or target.is_symlink():
    raise FileExistsError("GUI stop diagnostic requires fresh synthetic output")
target.mkdir(mode=0o700)
source = target / "synthetic-cancel-reference.mp4"
run_engine([str(tool_path("ffmpeg")), "-hide_banner", "-nostdin", "-loglevel", "error",
    "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=120",
    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "35", str(source)], timeout=120)
original_digest = digest_file(source)
app = QApplication([])
window = MainWindow()
window.show()
deadline = time.monotonic() + 240
while (window.workspace is None or window.maintenance_pending) and time.monotonic() < deadline:
    app.processEvents()
    time.sleep(.02)
if window.workspace is None or window.maintenance_pending:
    raise RuntimeError("Packaged workspace was not ready")
window.workspace_timer.stop()
page = window.media_tools_page
page.drop.filesSelected.emit([str(source)])
page.format.setCurrentText("webm")
page.profile.setCurrentIndex(page.profile.findData("archive"))
progress = []
window.tool_controller.progress.connect(lambda value: progress.append(value))
previous = {job.id for job in window.store.list_jobs()}
page.start_button.click()
deadline = time.monotonic() + 45
while window.tool_controller.busy and not any(0 < value < 1 for value in progress) and time.monotonic() < deadline:
    app.processEvents()
    time.sleep(.02)
running_observed = window.tool_controller.busy and any(0 < value < 1 for value in progress)
stop_enabled = page.cancel_button.isEnabled()
cancelled_at = time.monotonic()
if running_observed and stop_enabled:
    page.cancel_button.click()
else:
    window.tool_controller.cancel()  # Only the synthetic job created above.
deadline = time.monotonic() + 10
while window.tool_controller.busy and time.monotonic() < deadline:
    app.processEvents()
    time.sleep(.02)
elapsed = time.monotonic() - cancelled_at
records = [job for job in window.store.list_jobs() if job.id not in previous]
expected_output = window.workspace.outputs / (source.stem + "-ksi.webm")
checks = dict(real_start_button_dispatch=len(records) == 1,
              native_job_progress_observed=running_observed,
              real_stop_button_enabled=stop_enabled,
              stop_returns_responsive=not window.tool_controller.busy and elapsed < 5,
              job_cancelled=len(records) == 1 and records[0].status == JobStatus.CANCELLED,
              no_published_partial_output=not expected_output.exists(),
              original_source_preserved=digest_file(source) == original_digest)
window.close()
app.processEvents()
atomic_write_json(target / "result.local.json", dict(checks=checks, cancel_seconds=elapsed,
    source_commit=json.loads((resources / "build-provenance.json").read_bytes())["source_commit"],
    offline_manifest_sha256=digest_file(resources / "offline-manifest.json"),
    scope="Unmocked packaged UI start/stop during native encoding of synthetic media; not complete final release acceptance"))
print(json.dumps(dict(checks=checks, cancel_seconds=elapsed)))
if not all(checks.values()):
    raise RuntimeError("Actual GUI mid-flight stop checks failed")
