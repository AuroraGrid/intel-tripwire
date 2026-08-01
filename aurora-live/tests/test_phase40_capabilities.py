from __future__ import annotations

import unittest

from phase40_capabilities import reconciled_gaps, reconciled_manifest


def provider(name, *, layer="", operational=False, state="NOT_CONFIGURED", observations=0):
    return {
        "provider": name,
        "layer": layer,
        "state": state,
        "operational": operational,
        "observations": observations,
    }


class Phase40CapabilityTests(unittest.TestCase):
    def setUp(self):
        self.webcams = {"qualified_regions": 0, "total_online": 0, "total_registered": 0, "fully_qualified": False}
        self.imagery = {"qualified_regions": 0, "fully_qualified": False}
        self.source_health = {"feeds": [], "state": "NOT_CONFIGURED"}
        self.transport = {"providers": []}
        self.infrastructure = {"providers": [], "operational_layers": 0, "fully_operational": False}

    def test_manifest_marks_operational_market_live(self):
        markets = {
            "providers": [
                provider("coingecko-markets", layer="crypto", operational=True, state="ONLINE", observations=10),
                provider("configured-energy-feed", layer="energy", operational=False, state="NOT_CONFIGURED"),
            ],
            "operational_layers": 1,
            "fully_operational": False,
        }
        manifest = reconciled_manifest(
            webcam_coverage=self.webcams,
            imagery_baseline=self.imagery,
            unified_health=self.source_health,
            transport_health=self.transport,
            infrastructure_health=self.infrastructure,
            markets_health=markets,
        )
        by_key = {item["key"]: item for item in manifest["capabilities"]}
        self.assertEqual(manifest["phase"], 40)
        self.assertEqual(by_key["crypto"]["status"], "LIVE")
        self.assertEqual(by_key["energy"]["status"], "PARTIAL")
        self.assertEqual(by_key["crypto"]["status_source"], "runtime-evidence")

    def test_gaps_include_non_live_markets(self):
        gaps = reconciled_gaps(
            webcam_coverage=self.webcams,
            imagery_baseline=self.imagery,
            unified_health=self.source_health,
            transport_health=self.transport,
            infrastructure_health=self.infrastructure,
            markets_health={"providers": []},
            priority="P0",
        )
        all_gaps = reconciled_gaps(
            webcam_coverage=self.webcams,
            imagery_baseline=self.imagery,
            unified_health=self.source_health,
            transport_health=self.transport,
            infrastructure_health=self.infrastructure,
            markets_health={"providers": []},
        )
        p0_keys = {item["key"] for item in gaps["gaps"]}
        all_keys = {item["key"] for item in all_gaps["gaps"]}
        self.assertEqual(gaps["phase"], 40)
        # P0 market gaps (crypto is P1 in the product registry)
        self.assertIn("global-stocks", p0_keys)
        self.assertIn("energy", p0_keys)
        self.assertIn("prediction-markets", p0_keys)
        self.assertIn("crypto", all_keys)
        self.assertIn("currencies", all_keys)


if __name__ == "__main__":
    unittest.main()
