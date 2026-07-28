from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from phase37_complete import Phase37Application
from phase38_complete import Phase38Application
from phase38_transport import TransportObservation, TransportRegistry, TransportStore


class Phase38Tests(unittest.TestCase):
    def test_release_is_forward_compatible(self):
        self.assertTrue(issubclass(Phase38Application, Phase37Application))

    def test_provider_registration_does_not_qualify_transport(self):
        store = TransportStore(":memory:")
        registry = TransportRegistry(store)
        registry.register_provider({"provider": "aviation-fixture", "domain": "aviation"})
        coverage = store.coverage()
        self.assertFalse(coverage["fully_qualified"])
        self.assertEqual(coverage["qualified_domains"], 0)
        self.assertEqual(store.providers("aviation")[0]["state"], "NOT_CONFIGURED")

    def test_both_domains_require_online_provider_and_observation(self):
        store = TransportStore(":memory:")
        registry = TransportRegistry(store)
        for domain in ("aviation", "maritime"):
            provider = f"{domain}-fixture"
            registry.register_provider({"provider": provider, "domain": domain})
            registry.observe_provider(provider, {"successful": True, "freshness_seconds": 30})
            store.record(
                TransportObservation(
                    domain=domain,
                    provider=provider,
                    external_id=f"{domain}-1",
                    observed_at="2026-07-28T12:00:00Z",
                    event_time="2026-07-28T11:59:30Z",
                    latitude=1.0,
                    longitude=2.0,
                    state="FRESH",
                    payload={"fixture": True},
                    provenance={"source": "test-only"},
                )
            )
        coverage = store.coverage()
        self.assertTrue(coverage["fully_qualified"])
        self.assertEqual(coverage["qualified_domains"], 2)

    def test_transport_data_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "transport.sqlite3")
            first = TransportStore(path)
            first.record(
                TransportObservation(
                    domain="aviation",
                    provider="fixture",
                    external_id="flight-1",
                    observed_at="2026-07-28T12:00:00Z",
                    event_time="2026-07-28T11:59:00Z",
                    latitude=10.0,
                    longitude=20.0,
                    state="FRESH",
                    payload={"callsign": "TEST1"},
                    provenance={"license": "test-only"},
                )
            )
            second = TransportStore(path)
            rows = second.observations("aviation")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["payload"]["callsign"], "TEST1")
            self.assertEqual(rows[0]["provenance"]["license"], "test-only")

    def test_three_failures_force_provider_offline(self):
        store = TransportStore(":memory:")
        registry = TransportRegistry(store)
        registry.register_provider({"provider": "marine-fixture", "domain": "maritime"})
        result = None
        for _ in range(3):
            result = registry.observe_provider("marine-fixture", {"successful": False, "error": "fixture outage"})
        self.assertEqual(result["state"], "OFFLINE")
        self.assertEqual(result["consecutive_failures"], 3)


if __name__ == "__main__":
    unittest.main()
