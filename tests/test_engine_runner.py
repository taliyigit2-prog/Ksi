"""Actual isolated child processes; no user tools, files or running processes."""
import sys
import threading
import time
import unittest

from ksi_local.engine_runner import OperationCancelled, run_engine


class EngineRunnerTests(unittest.TestCase):
    def silent_live_child(self):
        return [sys.executable, "-B", "-c",
                "import os,time; os.close(1); os.close(2); time.sleep(30)"]

    def test_cancel_remains_responsive_after_child_closes_output(self):
        cancel = threading.Event()
        timer = threading.Timer(0.15, cancel.set)
        timer.start()
        started = time.monotonic()
        try:
            with self.assertRaises(OperationCancelled):
                run_engine(self.silent_live_child(), cancel=cancel, timeout=1)
            self.assertLess(time.monotonic() - started, 1.5)
        finally:
            timer.cancel()
            timer.join(timeout=1)

    def test_deadline_is_consistent_after_child_closes_output(self):
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            run_engine(self.silent_live_child(), timeout=0.2)
        self.assertLess(time.monotonic() - started, 1.5)
