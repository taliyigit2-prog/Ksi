"""Unmocked native packaged summary; not a complete distribution gate."""
import hashlib
import json
import os
import socket
import sys
import time
from pathlib import Path
from ksi_local.atomic_files import atomic_write_json
from ksi_local.bundle_runtime import OfflinePayload, bundle_root, digest_file
from ksi_local.network_policy import local_only_socket_guard
from ksi_local.ollama_client import OllamaClient
from ksi_local.ollama_runtime import managed_ollama, _is_ready
from ksi_local.subtitles import Cue
from ksi_local.summarization import summarize_cues

resources = bundle_root()
if resources is None:
    raise RuntimeError("Requires actual packaged interpreter")
import ksi_local
if not Path(ksi_local.__file__).resolve().is_relative_to(resources / "runtime/src"):
    raise RuntimeError("Diagnostic must import exact packaged application source")
payload = OfflinePayload.load(resources)
engine = payload.component("tool", "ollama")
models = Path(os.environ["KSI_STATE_DIRECTORY"]) / "KSI-Workspace/models/ollama"
base = "http://127.0.0.1:11459"
with socket.socket() as probe:
    probe.bind(("127.0.0.1", 11459))
cues = [Cue(1, "00:00:00,000", "00:00:10,000", "Program yalnız bilgisayarın dahili diskinde çalışır."),
    Cue(2, "00:00:10,000", "00:00:20,000", "Program 25 belgeyi yerel olarak işleyebilir."),
    Cue(3, "00:00:20,000", "00:00:30,000", "Kullanıcının dosyaları dış sunuculara gönderilmez.")]
started = time.monotonic()
with local_only_socket_guard(), managed_ollama(executable=str(engine), models_directory=models, base_url=base) as owned:
    if not owned:
        raise RuntimeError("Foreign daemon prohibited")
    result = summarize_cues(cues, client=OllamaClient(base), source_title="Yerel çalışma",
        source_reference="Synthetic diagnostic", transcript_sha256=hashlib.sha256(json.dumps([c.text for c in cues]).encode()).hexdigest())
    checks = dict(nonempty=bool(result.claims), number_preserved="25" in result.markdown,
        source_ids_valid=all(set(claim.source_ids) <= {"S000001", "S000002", "S000003"} for claim in result.claims),
        local_disk_fact="disk" in result.markdown.lower(), privacy_fact=any(word in result.markdown.lower() for word in ("sunucu", "gönderil")))
checks["owned_daemon_stopped"] = not _is_ready(base)
atomic_write_json(Path(sys.argv[1]), dict(scope="Actual native packaged product summary; not full distribution acceptance",
    source_commit=json.loads((resources / "build-provenance.json").read_bytes())["source_commit"],
    manifest_sha256=digest_file(resources / "offline-manifest.json"), seconds=time.monotonic()-started,
    checks=checks, markdown=result.markdown, quality=result.quality, traceability=result.traceability))
if not all(checks.values()):
    raise RuntimeError("Packaged summary reference checks failed")
print(json.dumps(dict(checks=checks, seconds=time.monotonic()-started)))
