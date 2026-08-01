from __future__ import annotations

import unittest

from phase39_capabilities import reconciled_gaps, reconciled_manifest


def provider(name, *, domain="", layer="", operational=False, state="NOT_CONFIGURED", observations=0):
    value = {
        "provider": name,
        "state": state,
        "operational": operational,
        "observations": observations,
    }
    if domain:
        value["domain"] = domain
    if layer:
        value["layer"] = layer
    return value


class Phase39CapabilityTests(unittest.TestCase):
    def setUp(self):
        self.webcams = {"qualified_regions": 0, "total_online": 0, "total_registered": 0, "fully_qualified": False}
        self.imagery = {"qualified_regions": 0, "fully_qualified": False}
        self.source_health = {"feeds": [], "state": "NOT_CONFIGURED"}

    def test_manifest_phase_and_runtime_statuses(self):
        transport = {
            "providers": [
                provider("aviationweather-gov", domain="aviation", operational=True, state="ONLINE", observations=12),
                provider("aisstream", domain="maritime", operational=False, state="NOT_CONFIGURED"),
            ]
        }
        infrastructure = {
            "providers": [
                provider("nws-active-alerts", layer="severe_weather", operational=True, state="ONLINE", observations=20),
                provider("nws-power-outage-alerts", layer="outage", operational=False, state="NOT_CONFIGURED"),
            ],
            "operational_layers": 1,
            "fully_operational": False,
        }
        manifest = reconciled_manifest(
            webcam_coverage=self.webcams,
            imagery_baseline=self.imagery,
            unified_health=self.source_health,
            transport_health=transport,
            infrastructure_health=infrastructure,
        )
        by_key = {item["key"]: item for item in manifest["capabilities"]}
        self.assertEqual(manifest["phase"], 39)
        self.assertEqual(by_key["aviation"]["status"], "LIVE")
        self.assertEqual(by_key["maritime"]["status"], "PARTIAL")
        self.assertEqual(by_key["weather"]["status"], "LIVE")
        self.assertEqual(by_key["internet-outages"]["status"], "PARTIAL")
        self.assertEqual(by_key["infrastructure"]["status"], "PARTIAL")
        self.assertEqual(by_key["aviation"]["status_source"], "runtime-evidence")

    def test_gaps_follow_effective_status_not_static_declaration(self):
        transport = {"providers": []}
        infrastructure = {"providers": [], "operational_layers": 0, "fully_operational": False}
        gaps = reconciled_gaps(
            webcam_coverage=self.webcams,
            imagery_baseline=self.imagery,
            unified_health=self.source_health,
            transport_health=transport,
            infrastructure_health=infrastructure,
            priority="P0",
        )
        keys = {item["key"] for item in gaps["gaps"]}
        self.assertEqual(gaps["phase"], 39)
        self.assertIn("aviation", keys)
        self.assertIn("maritime", keys)
        self.assertIn("weather", keys)
        self.assertIn("internet-outages", keys)
        self.assertIn("bgp", keys)
        self.assertIn("sanctions", keys)


if __name__ == "__main__":
    unittest.main()
