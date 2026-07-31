from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from phase39_secure import SecureConfiguredJSONAdapter


class Phase39SecureTests(unittest.TestCase):
    def test_api_key_never_enters_observation_or_provenance(self):
        adapter = SecureConfiguredJSONAdapter(
            name="power-fixture",
            layer="power",
            url_env="AURORA_POWER_FEED_URL",
            license_env="AURORA_POWER_FEED_LICENSE",
            api_key_env="AURORA_POWER_API_KEY",
        )
        secret = "fixture-secret-value"
        with patch.dict(
            os.environ,
            {
                "AURORA_POWER_FEED_URL": "https://example.invalid/data?api_key={api_key}",
                "AURORA_POWER_FEED_LICENSE": "test only",
                "AURORA_POWER_API_KEY": secret,
            },
            clear=True,
        ), patch("phase39_secure._json", return_value={"data": [{"id": "1", "title": "Power fixture"}]}) as mocked:
            rows = adapter.fetch()
        self.assertIn(secret, mocked.call_args.args[0])
        persisted = json.dumps(rows[0].value(), sort_keys=True)
        self.assertNotIn(secret, persisted)
        self.assertEqual(rows[0].source_url, "env://AURORA_POWER_FEED_URL")
        self.assertFalse(rows[0].provenance["request_url_persisted"])


if __name__ == "__main__":
    unittest.main()
