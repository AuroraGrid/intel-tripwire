from __future__ import annotations

import unittest

from phase42_complete import Phase42Application
from phase43_complete import Phase43Application
from phase43_public import AbuseLimiter, public_config


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

    def test_public_config_never_includes_secrets(self):
        config = public_config()
        self.assertTrue(config["no_paywall"])
        self.assertTrue(config["abuse_controls"])
        self.assertNotIn("secret", str(config).lower() + "safe")


if __name__ == "__main__":
    unittest.main()
