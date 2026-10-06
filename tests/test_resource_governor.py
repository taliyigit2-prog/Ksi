"""Cross-thread model operation arbitration; private state is temporary."""

import os
import tempfile
import threading
import unittest
from unittest.mock import patch

from ksi_local.resource_governor import active_model_descriptor, single_model_lock


class ResourceGovernorTests(unittest.TestCase):
    def test_nested_model_calls_share_lock(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {"KSI_STATE_DIRECTORY": temporary}):
            with single_model_lock():
                descriptor = active_model_descriptor()
                with single_model_lock():
                    self.assertEqual(active_model_descriptor(), descriptor)
            self.assertIsNone(active_model_descriptor())

    def test_other_thread_cannot_load_second_model(self):
        errors = []

        def contender():
            try:
                with single_model_lock():
                    errors.append("unexpected acquisition")
            except RuntimeError:
                errors.append("blocked")

        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {"KSI_STATE_DIRECTORY": temporary}):
            with single_model_lock():
                thread = threading.Thread(target=contender)
                thread.start()
                thread.join(timeout=2)
                self.assertFalse(thread.is_alive())
        self.assertEqual(errors, ["blocked"])
