from __future__ import annotations

import unittest

from phase40_capabilities import reconciled_gaps, reconciled_manifest


def provider(name, domain, *, operational=False, state="NOT_CONFIGURED", observations=0, event_age_seconds=0):
    return {
        "provider": name,
        "domain": domain,
        "state": state,
        "operational": operational,
        "observations": observations,
        "event_age_seconds": event_age_seconds,
    }


class Phase40CapabilityTests(unittest.TestCase):
    def setUp(self):
        self.webcams = {"qualified_regions": 0, "total_online": 0, "total_registered": 0, "fully_qualified": False}
        self.imagery = {"qualified_regions": 0, "fully_qualified": False}
        self.sources = {"feeds": [], "state": "NOT_CONFIGURED"}
        self.transport = {"providers": []}
        self.infrastructure = {"providers": [], "operational_layers": 0, "fully_operational": False}

    def test_manifest_uses_market_runtime_evidence(self):
        markets = {
            "providers": [
                provider("alpha-vantage-global-quote", "equities", operational=False),
                provider("eia-api-v2", "energy", operational=False),
                provider("world-bank-pink-sheet", "commodities", operational=True, state="ONLINE", observations=6),
                provider("ecb-reference-rates", "fx", operational=True, state="ONLINE", observations=14),
                provider("coinbase-exchange-ticker", "crypto", operational=True, state="ONLINE", observations=2),
                provider("world-bank-indicators-v2", "economic_indicators", operational=True, state="ONLINE", observations=18),
                provider("kalshi-public-markets", "prediction_markets", operational=True, state="ONLINE", observations=100),
            ],
            "operational_domains": 5,
            "fully_operational": False,
        }
        manifest = reconciled_manifest(
            webcam_coverage=self.webcams,
            imagery_baseline=self.imagery,
            unified_health=self.sources,
            transport_health=self.transport,
            infrastructure_health=self.infrastructure,
            market_health=markets,
        )
        by_key = {item["key"]: item for item in manifest["capabilities"]}
        self.assertEqual(manifest["phase"], 40)
        self.assertEqual(by_key["global-stocks"]["status"], "PARTIAL")
        self.assertEqual(by_key["energy"]["status"], "PARTIAL")
        self.assertEqual(by_key["commodities"]["status"], "LIVE")
        self.assertEqual(by_key["currencies"]["status"], "LIVE")
        self.assertEqual(by_key["crypto"]["status"], "LIVE")
        self.assertEqual(by_key["economic-indicators"]["status"], "LIVE")
        self.assertEqual(by_key["prediction-markets"]["status"], "LIVE")
        self.assertEqual(by_key["prediction-markets"]["status_source"], "runtime-evidence")

    def test_gaps_follow_effective_market_status(self):
        markets = {"providers": [], "operational_domains": 0, "fully_operational": False}
        gaps = reconciled_gaps(
            webcam_coverage=self.webcams,
            imagery_baseline=self.imagery,
            unified_health=self.sources,
            transport_health=self.transport,
            infrastructure_health=self.infrastructure,
            market_health=markets,
            priority="P0",
        )
        keys = {item["key"] for item in gaps["gaps"]}
        self.assertEqual(gaps["phase"], 40)
        self.assertIn("global-stocks", keys)
        self.assertIn("energy", keys)
        self.assertIn("prediction-markets", keys)


if __name__ == "__main__":
    unittest.main()
