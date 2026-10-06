import unittest
from urllib.request import Request
from unittest.mock import MagicMock, patch

from ksi_local.network_policy import LocalModelRedirect, NetworkPolicyError, open_loopback


class LocalModelHTTPTests(unittest.TestCase):
    def test_external_credentials_and_queries_rejected_before_any_transport(self):
        credential_url = "http://" + "user:password@" + "localhost/api/generate"
        for value in ("https://example.com/api/generate", credential_url, "http://localhost/api/generate?token=example"):
            with self.subTest(value=value), patch("urllib.request.build_opener") as transport:
                with self.assertRaises(NetworkPolicyError):
                    open_loopback(value, timeout=1)
                transport.assert_not_called()

    def test_every_model_redirect_is_rejected_including_other_loopback_services(self):
        handler = LocalModelRedirect()
        for target in ("https://example.com/upload", "http://localhost:9000/upload"):
            with self.subTest(target=target), self.assertRaises(NetworkPolicyError):
                handler.redirect_request(Request("http://127.0.0.1:11435/api/generate"), None, 302, "redirect", {}, target)

    def test_loopback_opener_has_an_explicit_empty_proxy_configuration(self):
        opener = MagicMock()
        with patch("urllib.request.ProxyHandler") as proxy, patch("urllib.request.build_opener", return_value=opener):
            open_loopback("http://127.0.0.1:11435/api/tags", timeout=1)
            proxy.assert_called_once_with({})
            opener.open.assert_called_once_with("http://127.0.0.1:11435/api/tags", timeout=1)
