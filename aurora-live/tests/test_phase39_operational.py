from __future__ import annotations

import unittest

from phase39_infrastructure import InfrastructureObservation, ProviderRun, _now
from phase39_operational import OperationalInfrastructureStore


class Phase39OperationalTests(unittest.TestCase):
    def test_old_event_can_still_be_freshly_retrieved(self):
        store = OperationalInfrastructureStore(":memory:")
        now = _now()
        store.upsert_provider(
            {
                "provider": "catalog-fixture",
                "layer": "cyber",
                "state": "ONLINE",
                "last_attempt_at": now,
                "last_success_at": now,
                "consecutive_failures": 0,
                "freshness_seconds": 31536000,
                "last_error": "",
                "completeness_note": "test",
                "license_note": "test",
            }
        )
        store.record(
            InfrastructureObservation(
                layer="cyber",
                provider="catalog-fixture",
                external_id="CVE-OLD",
                observed_at=now,
                event_time="2025-01-01T00:00:00Z",
                severity="HIGH",
                title="Old catalog entry",
                summary="Still present in a freshly retrieved official catalog",
                source_url="https://example.invalid",
                payload={},
                provenance={"source": "test"},
            )
        )
        store.record_run(ProviderRun("catalog-fixture", "cyber", True, True, 1, started_at=now, completed_at=now))
        provider = store.health(max_age_seconds=300)["providers"][0]
        self.assertTrue(provider["retrieval_fresh"])
        self.assertTrue(provider["operational"])
        self.assertEqual(provider["event_freshness_seconds"], 31536000)


if __name__ == "__main__":
    unittest.main()
