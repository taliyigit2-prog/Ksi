"""Real owned process cancellation/crash recovery; never user jobs or processes."""
import json
import os
import select
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import ksi_local
from ksi_local.atomic_files import atomic_write_json
from ksi_local.bundle_runtime import OfflinePayload, bundle_root, digest_file, tool_path
from ksi_local.engine_runner import OperationCancelled, run_engine
from ksi_local.job_leases import lease_is_active
from ksi_local.job_store import JobStatus, JobStore
from ksi_local.native_processor import is_rosetta_translated
from ksi_local.ollama_runtime import managed_ollama, _is_ready
from ksi_local.resource_governor import active_model_descriptor, single_model_lock

resources = bundle_root()
if resources is None or not Path(ksi_local.__file__).resolve().is_relative_to(resources / "runtime/src"):
    raise RuntimeError("Exact packaged source required")
state = Path(os.environ["KSI_STATE_DIRECTORY"])
from ksi_local.internal_storage import validate_internal_path
from ksi_local.bundle_runtime import host_architecture
validate_internal_path(state)
if state.exists() or state.is_symlink():
    raise FileExistsError("Lifecycle probe requires a fresh isolated state")
state.mkdir(mode=0o700)
checks = {}
cancel = threading.Event()
progress_seen = []
sentinel = subprocess.Popen(["/bin/sleep", "45"], start_new_session=True)
started = time.monotonic()
cancel_requested_at = []
def progressed(line):
    if line.startswith("frame="):
        progress_seen.append(line)
        if not cancel_requested_at:
            cancel_requested_at.append(time.monotonic())
        cancel.set()
try:
    with single_model_lock():
        try:
            run_engine([str(tool_path("ffmpeg")), "-hide_banner", "-nostdin", "-loglevel", "error",
                "-re", "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=5:duration=60",
                "-progress", "pipe:1", "-f", "null", "-"], cancel=cancel, on_line=progressed, timeout=15)
        except OperationCancelled:
            checks["midflight-native-cancel"] = bool(progress_seen) and time.monotonic()-cancel_requested_at[0] < 5
        else:
            checks["midflight-native-cancel"] = False
    checks["cancel-releases-model-lock"] = active_model_descriptor() is None
    with single_model_lock():
        checks["subsequent-model-lock-acquired"] = active_model_descriptor() is not None
    checks["unrelated-owned-fixture-process-survives"] = sentinel.poll() is None
finally:
    sentinel.terminate()
    sentinel.wait(timeout=3)

class ForeignFixture(BaseHTTPRequestHandler):
    def do_GET(self):
        content = b'{"models":[]}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)
    def log_message(self, *_args):
        pass
server = ThreadingHTTPServer(("127.0.0.1", 0), ForeignFixture)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
base = f"http://127.0.0.1:{server.server_port}"
try:
    payload = OfflinePayload.load(resources)
    try:
        with managed_ollama(executable=str(payload.component("tool", "ollama")), models_directory=state / "unused", base_url=base):
            pass
    except RuntimeError as error:
        checks["foreign-listener-rejected"] = "başka bir sunucu" in str(error)
    else:
        checks["foreign-listener-rejected"] = False
    checks["foreign-listener-preserved"] = _is_ready(base) and thread.is_alive()
finally:
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)

store = JobStore(state / "jobs.sqlite3")
identifier = "synthetic-native-crash-fixture"
store.create_job(job_id=identifier, source=str(state / "synthetic.mp4"), source_language="en",
                 want_subtitle=False, want_summary=False, want_dub=False,
                 job_directory=state / "jobs/synthetic", job_kind="media")
store.claim_queued_job(identifier, stage="local_tools")
code = "from pathlib import Path; import sys,time; from ksi_local.job_leases import execution_lease\nwith execution_lease(Path(sys.argv[1]),sys.argv[2]):\n print('LEASE_READY',flush=True); time.sleep(45)"
worker = subprocess.Popen([sys.executable, "-B", "-c", code, str(state), identifier],
    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
try:
    if not select.select([worker.stdout], [], [], 10)[0] or worker.stdout.readline().strip() != b"LEASE_READY":
        raise RuntimeError("Owned crash fixture did not acquire its real job lease")
    checks["live-job-not-recovered"] = lease_is_active(state, identifier) and store.recover_interrupted_jobs(workspace_available=True) == 0
    worker.kill()  # ONLY the exact synthetic worker created above, no other PID.
    worker.wait(timeout=3)
    checks["crash-releases-job-lease"] = not lease_is_active(state, identifier)
    checks["crashed-job-requeued"] = store.recover_interrupted_jobs(workspace_available=True) == 1 and store.get_job(identifier).status == JobStatus.QUEUED
finally:
    if worker.poll() is None:
        worker.kill()
        worker.wait(timeout=3)
    worker.stdout.close()
    worker.stderr.close()
atomic_write_json(Path(sys.argv[1]), dict(source_commit=json.loads((resources / "build-provenance.json").read_bytes())["source_commit"],
    architecture=host_architecture(), offline_manifest_sha256=digest_file(resources / "offline-manifest.json"),
    native_process=True, rosetta_translated=is_rosetta_translated(), checks=checks,
    scope="Unmocked packaged lifecycle mechanisms and synthetic foreign-listener/crash fixtures; not complete final release acceptance"))
print(json.dumps(dict(checks=checks)))
if not all(checks.values()):
    raise RuntimeError("Real lifecycle mechanism checks failed")
