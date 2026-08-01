from __future__ import annotations

import ipaddress
import socket
import unittest
from unittest.mock import patch

from webhook_security import resolve_public_https_url


def _ai(ip: str, port: int = 443):
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    return [(family, socket.SOCK_STREAM, 0, "", (ip, port))]


class WebhookSecurityTests(unittest.TestCase):
    def test_rejects_http(self):
        with self.assertRaises(ValueError):
            resolve_public_https_url("http://example.com/hook")

    def test_rejects_literal_private_ip(self):
        with self.assertRaises(ValueError):
            resolve_public_https_url("https://127.0.0.1/hook")
        with self.assertRaises(ValueError):
            resolve_public_https_url("https://10.0.0.5/hook")

    def test_rejects_dns_to_private(self):
        def resolver(host, port, type=0):  # noqa: A002
            return _ai("127.0.0.1", port)

        with self.assertRaises(ValueError):
            resolve_public_https_url("https://evil.example/hook", resolver=resolver)

    def test_accepts_public_resolution(self):
        def resolver(host, port, type=0):  # noqa: A002
            return _ai("93.184.216.34", port)

        url = resolve_public_https_url("https://hooks.example.com/aurora", resolver=resolver)
        self.assertEqual(url, "https://hooks.example.com/aurora")

    def test_rejects_metadata_hostname(self):
        with self.assertRaises(ValueError):
            resolve_public_https_url("https://metadata.google.internal/latest")


if __name__ == "__main__":
    unittest.main()
