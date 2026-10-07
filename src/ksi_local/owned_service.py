"""Supervise one owned server; parent pipe EOF stops its entire process group."""

from __future__ import annotations

import os
import json
import selectors
import signal
import subprocess
import sys


def supervise(command: list[str], *, report_pid: bool = False) -> int:
    stopped = False

    def stop(_signal, _frame):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    # The supervisor retains inherited reader/model/job locks. The actual
    # external server has no inherited descriptors or parent-liveness pipe.
    child = subprocess.Popen(command, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True, close_fds=True)
    try:
        if report_pid:
            print(json.dumps({"pid": child.pid}), flush=True)
        with selectors.DefaultSelector() as selector:
            selector.register(sys.stdin.buffer, selectors.EVENT_READ)
            while not stopped and child.poll() is None:
                for key, _ in selector.select(timeout=0.2):
                    if not os.read(key.fileobj.fileno(), 4096):
                        stopped = True
        return child.returncode if child.returncode is not None else 0
    finally:
        # Target only the group created above, never a pre-existing daemon.
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        # A runner can outlive its server: clean the owned group even when the
        # leader already exited. No session/group identity is derived from disk.
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait(timeout=5)


def main() -> int:
    if len(sys.argv) not in {3, 4} or sys.argv[2] != "serve" or not os.path.isabs(sys.argv[1]) or (len(sys.argv) == 4 and sys.argv[3] != "--report-pid"):
        raise ValueError("Owned service requires one explicit server executable.")
    return supervise(sys.argv[1:3], report_pid=len(sys.argv) == 4)


if __name__ == "__main__":
    raise SystemExit(main())
