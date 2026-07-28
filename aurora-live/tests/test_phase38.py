from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from phase37_complete import Phase37Application
from phase38_complete import Phase38Application
from phase38_providers import AISStreamAdapter, AviationWeatherAdapter, TransportProviderCoordinator
from phase38_transport import TransportObservation, TransportRegistry, TransportStore


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class _Socket:
    def __init__(self, rows):
        self.rows = iter(rows)
        self.sent = ""
        self.closed = False

    def send(self, payload):
        self.sent = payload

    def recv(self):
        return json.dumps(next(self.rows))

    def close(self):
        self.closed = True


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

    def test_aviationweather_keyless_adapter_records_metar(self):
        payload = [{"icaoId": "KJFK", "lat": 40.64, "lon": -73.78, "reportTime": "2026-07-28T12:00:00Z"}]
        adapter = AviationWeatherAdapter(opener=lambda request, timeout: _Response(payload))
        rows = adapter.observations(adapter.fetch())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].provider, "aviationweather-gov")
        self.assertFalse(rows[0].provenance["key_required"])

    def test_aisstream_reads_secret_only_from_environment(self):
        socket = _Socket([
            {
                "MessageType": "PositionReport",
                "MetaData": {"MMSI": 123456789, "latitude": 10.0, "longitude": 20.0, "time_utc": "2026-07-28T12:00:00Z"},
                "Message": {"PositionReport": {"Sog": 12.0}},
            }
        ])
        with patch.dict(os.environ, {"AURORA_AISSTREAM_API_KEY": "test-secret"}, clear=False):
            adapter = AISStreamAdapter(connection_factory=lambda endpoint, timeout: socket)
            raw = adapter.fetch(max_messages=1)
            rows = adapter.observations(raw)
        self.assertEqual(len(rows), 1)
        self.assertNotIn("test-secret", json.dumps(rows[0].value()))
        self.assertEqual(rows[0].provenance["credential_env"], "AURORA_AISSTREAM_API_KEY")
        self.assertTrue(socket.closed)

    def test_missing_ais_key_never_qualifies_maritime(self):
        with patch.dict(os.environ, {}, clear=True):
            coordinator = TransportProviderCoordinator(TransportStore(":memory:"))
            result = coordinator.run_maritime(max_messages=1)
        self.assertFalse(result.successful)
        self.assertIn("not configured", result.error)
        self.assertFalse(coordinator.store.coverage()["fully_qualified"])


if __name__ == "__main__":
    unittest.main()
