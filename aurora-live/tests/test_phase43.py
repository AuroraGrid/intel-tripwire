from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from durable_public import DurableAbuseLimiter, DurableNotificationStore
from phase42_complete import Phase42Application
from phase43_complete import Phase43Application
from phase43_public import AbuseLimiter, public_config
from storage import Store


class Phase43Tests(unittest.TestCase):
    def test_release_is_forward_compatible(self):
        self.assertTrue(issubclass(Phase43Application, Phase42Application))

    def test_rate_limiter_blocks_after_limit(self):
        limiter = AbuseLimiter(limit=3, window_seconds=60)
        self.assertTrue(limiter.check("client").allowed)
        self.assertTrue(limiter.check("client").allowed)
        self.assertTrue(limiter.check("client").allowed)
        blocked = limiter.check("client")
        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.remaining, 0)

    def test_durable_rate_limiter_shared_via_db(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / "rate.db")
            a = DurableAbuseLimiter(store=store, limit=2, window_seconds=60)
            b = DurableAbuseLimiter(store=store, limit=2, window_seconds=60)
            self.assertTrue(a.check("ip1").allowed)
            self.assertTrue(b.check("ip1").allowed)
            blocked = a.check("ip1")
            self.assertFalse(blocked.allowed)

    def test_durable_notification_store(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp) / "push.db")
            notes = DurableNotificationStore(store=store)
            with patch("webhook_security.socket.getaddrinfo", return_value=[(2, 1, 0, "", ("93.184.216.34", 443))]):
                result = notes.add({"endpoint": "https://push.example.com/sub", "keys": {"p256dh": "x"}})
            self.assertTrue(result["stored"])
            self.assertEqual(notes.count(), 1)

    def test_public_config_never_includes_secrets(self):
        config = public_config()
        self.assertTrue(config["no_paywall"])
        self.assertTrue(config["abuse_controls"])
        self.assertNotIn("secret", str(config).lower() + "safe")


if __name__ == "__main__":
    unittest.main()
