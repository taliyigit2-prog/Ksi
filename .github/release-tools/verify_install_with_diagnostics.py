"""Invoke unchanged product installer checks and safely expose failure evidence."""
import argparse
import importlib.util
import json
import shutil
import sys
from pathlib import Path
from ksi_local.atomic_files import atomic_write_json
from ksi_local.privacy import redact_sensitive_text

parser = argparse.ArgumentParser()
for name in ('repository', 'application', 'media', 'destination'):
    parser.add_argument(name, type=Path)
args = parser.parse_args()
sys.path.insert(0, str(args.repository.absolute() / 'scripts'))
from verify_native_offline_install import verify
try:
    print(json.dumps(verify(args.application, args.media, args.destination)))
except Exception as error:
    spec = importlib.util.spec_from_file_location('scanner', args.repository / 'scripts/audit_github_artifacts.py')
    scanner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scanner)
    logs = {}
    for name in ('first-offline-gui.log', 'installed-speech.log', 'ordinary-launcher.log'):
        path = args.destination / name
        if path.is_file() and not path.is_symlink():
            with path.open('rb') as stream:
                stream.seek(max(0, path.stat().st_size - 16384))
                data = redact_sensitive_text(stream.read().decode('utf-8', errors='replace')).encode()
            for pattern in scanner.COMPILED.values():
                data = pattern.sub(b'[redacted]', data)
            logs[name] = data.decode('utf-8', errors='replace')
    diagnostic = dict(success=False, exception_type=type(error).__name__,
                      available_disk_bytes=shutil.disk_usage(args.repository).free,
                      synthetic_diagnostic_tails=logs)
    args.destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_write_json(args.destination / 'failure-diagnostic.json', diagnostic)
    print(json.dumps(diagnostic), flush=True)
    raise
