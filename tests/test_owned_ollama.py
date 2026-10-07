import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.request import Request

from ksi_local.network_policy import NetworkPolicyError
from ksi_local.owned_ollama import _sockets, open_owned_loopback, owned_server_identity, verify_connection_owner


class OwnedOllamaTests(unittest.TestCase):
    def test_no_active_owned_session_cannot_send_any_model_text(self):
        request = Request("http://127.0.0.1:11435/api/generate", data=b"synthetic prompt")
        with patch("ksi_local.owned_ollama.urllib.request.build_opener") as create:
            with self.assertRaises(NetworkPolicyError):
                open_owned_loopback(request, timeout=1)
            create.assert_not_called()

    def test_another_endpoint_cannot_reuse_owned_identity(self):
        request = Request("http://127.0.0.1:11436/api/generate", data=b"synthetic prompt")
        with owned_server_identity(12345, "http://127.0.0.1:11435"):
            with self.assertRaises(NetworkPolicyError):
                open_owned_loopback(request, timeout=1)

    def test_only_exact_established_reverse_socket_is_authorized(self):
        connection = MagicMock()
        connection.getsockname.return_value = ("127.0.0.1", 54321)
        connection.getpeername.return_value = ("127.0.0.1", 11435)
        with patch("ksi_local.owned_ollama._sockets", return_value=["p12345", "n127.0.0.1:11435->127.0.0.1:54321"]) as sockets:
            verify_connection_owner(12345, connection)
        sockets.assert_called_once_with(12345, 11435, "ESTABLISHED")

    def test_listener_or_other_connection_is_not_ownership_proof(self):
        connection = MagicMock()
        connection.getsockname.return_value = ("127.0.0.1", 54321)
        connection.getpeername.return_value = ("127.0.0.1", 11435)
        with (patch("ksi_local.owned_ollama._sockets", return_value=["p12345", "n127.0.0.1:11435->127.0.0.1:54322"]),
                patch("ksi_local.owned_ollama.time.monotonic", side_effect=[0, 0, 3]),
                patch("ksi_local.owned_ollama.time.sleep")):
            with self.assertRaises(NetworkPolicyError):
                verify_connection_owner(12345, connection)

    def test_process_inspection_failure_is_closed_not_assumed_owned(self):
        with patch("ksi_local.owned_ollama.subprocess.run", side_effect=OSError("unavailable")):
            self.assertEqual(_sockets(12345, 11435, "LISTEN"), [])
        with patch("ksi_local.owned_ollama.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="p54321\nn127.0.0.1:11435\n")):
            self.assertEqual(_sockets(12345, 11435, "LISTEN"), [])

    def test_owned_identity_is_removed_when_context_exits(self):
        request = Request("http://127.0.0.1:11435/api/generate", data=b"synthetic prompt")
        with owned_server_identity(12345, "http://127.0.0.1:11435"):
            pass
        with self.assertRaises(NetworkPolicyError):
            open_owned_loopback(request, timeout=1)
