import signal
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ksi_local.owned_service import supervise
from ksi_local.ollama_runtime import managed_ollama


class OwnedServiceTests(unittest.TestCase):
    def test_parent_pipe_eof_stops_only_the_created_service_group(self):
        child = MagicMock(pid=54321)
        child.poll.return_value = None
        child.returncode = None
        pipe = MagicMock()
        pipe.fileno.return_value = 7
        selector = MagicMock()
        selector.__enter__.return_value = selector
        selector.select.return_value = [(SimpleNamespace(fileobj=pipe), None)]
        with patch("ksi_local.owned_service.signal.signal"), patch("ksi_local.owned_service.subprocess.Popen", return_value=child) as launch, patch("ksi_local.owned_service.selectors.DefaultSelector", return_value=selector), patch("ksi_local.owned_service.os.read", return_value=b""), patch("ksi_local.owned_service.os.killpg") as kill:
            self.assertEqual(supervise(["/verified/ollama", "serve"]), 0)
        self.assertTrue(launch.call_args.kwargs["start_new_session"])
        self.assertTrue(launch.call_args.kwargs["close_fds"])
        self.assertEqual(kill.call_args_list[0].args, (54321, signal.SIGTERM))
        self.assertEqual(kill.call_args_list[1].args, (54321, signal.SIGKILL))

    def test_packaged_app_refuses_foreign_ready_server_without_stopping_it(self):
        with patch("ksi_local.ollama_runtime._is_ready", return_value=True), patch("ksi_local.ollama_runtime.bundle_root", return_value=Path("/sealed/resources")), patch("ksi_local.ollama_runtime.subprocess.Popen") as launch:
            with self.assertRaises(RuntimeError):
                with managed_ollama(executable="/verified/ollama", models_directory="/sealed/models"):
                    self.fail("Foreign model server cannot become the packaged engine.")
            launch.assert_not_called()
