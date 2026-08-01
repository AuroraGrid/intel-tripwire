from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from phase37_complete import Phase37Application
from phase38_complete import Phase38Application
from phase38_providers import AISStreamAdapter, AviationWeatherAdapter, ProviderRun, TransportProviderCoordinator
from phase38_transport import TransportObservation, TransportRegistry, TransportStore
from phase38_worker import TransportOperationalWorker


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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


class _Coordinator:
    def __init__(self, successful=True):
        self.successful = successful
        self.calls = []

    def _run(self, provider, domain):
        self.calls.append(provider)
        now = _now()
        return ProviderRun(
            provider=provider,
            domain=domain,
            successful=self.successful,
            observations=1 if self.successful else 0,
            error="" if self.successful else "fixture failure",
            started_at=now,
            completed_at=now,
            duration_ms=1,
        )

    def run_aviation(self, **kwargs):
        return self._run("aviationweather-gov", "aviation")

    def run_maritime(self, **kwargs):
        return self._run("aisstream", "maritime")


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

    def test_both_domains_require_recent_run_online_provider_and_observation(self):
        store = TransportStore(":memory:")
        registry = TransportRegistry(store)
        now = _now()
        for domain in ("aviation", "maritime"):
            provider = f"{domain}-fixture"
            registry.register_provider({"provider": provider, "domain": domain})
            registry.observe_provider(provider, {"successful": True, "freshness_seconds": 30, "observed_at": now})
            store.record(
                TransportObservation(
                    domain=domain,
                    provider=provider,
                    external_id=f"{domain}-1",
                    observed_at=now,
                    event_time=now,
                    latitude=1.0,
                    longitude=2.0,
                    state="FRESH",
                    payload={"fixture": True},
                    provenance={"source": "test-only"},
                )
            )
            store.record_provider_run(
                {
                    "provider": provider,
                    "domain": domain,
                    "started_at": now,
                    "completed_at": now,
                    "successful": True,
                    "observations": 1,
                    "duration_ms": 1,
                }
            )
        coverage = store.coverage()
        self.assertTrue(coverage["fully_qualified"])
        self.assertEqual(coverage["qualified_domains"], 2)

    def test_transport_data_and_runs_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "transport.sqlite3")
            first = TransportStore(path)
            now = _now()
            first.record(
                TransportObservation(
                    domain="aviation",
                    provider="fixture",
                    external_id="flight-1",
                    observed_at=now,
                    event_time=now,
                    latitude=10.0,
                    longitude=20.0,
                    state="FRESH",
                    payload={"callsign": "TEST1"},
                    provenance={"license": "test-only"},
                )
            )
            first.record_provider_run(
                {
                    "provider": "fixture",
                    "domain": "aviation",
                    "successful": True,
                    "observations": 1,
                    "duration_ms": 5,
                    "started_at": now,
                    "completed_at": now,
                }
            )
            second = TransportStore(path)
            rows = second.observations("aviation")
            runs = second.provider_runs("aviation", "fixture")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["payload"]["callsign"], "TEST1")
            self.assertEqual(rows[0]["provenance"]["license"], "test-only")
            self.assertEqual(len(runs), 1)
            self.assertTrue(runs[0]["successful"])
            # Close DB connections to avoid Windows file locks during tempdir cleanup
            first.close()
            second.close()
            import gc
            gc.collect()

    def test_duplicate_external_id_is_idempotent(self):
        store = TransportStore(":memory:")
        now = _now()
        observation = TransportObservation(
            domain="maritime",
            provider="fixture",
            external_id="ais:1:now",
            observed_at=now,
            event_time=now,
            latitude=1.0,
            longitude=2.0,
            state="FRESH",
            payload={},
            provenance={},
        )
        first = store.record(observation)
        second = store.record(observation)
        self.assertEqual(first, second)
        self.assertEqual(len(store.observations("maritime")), 1)

    def test_stale_success_is_not_operational(self):
        store = TransportStore(":memory:")
        registry = TransportRegistry(store)
        old = "2000-01-01T00:00:00Z"
        registry.register_provider({"provider": "old-fixture", "domain": "maritime"})
        registry.observe_provider("old-fixture", {"successful": True, "freshness_seconds": 1, "observed_at": old})
        store.record(
            TransportObservation(
                domain="maritime",
                provider="old-fixture",
                external_id="old-1",
                observed_at=old,
                event_time=old,
                latitude=1.0,
                longitude=2.0,
                state="FRESH",
                payload={},
                provenance={},
            )
        )
        store.record_provider_run(
            {
                "provider": "old-fixture",
                "domain": "maritime",
                "successful": True,
                "observations": 1,
                "started_at": old,
                "completed_at": old,
            }
        )
        health = store.health(max_age_seconds=60)
        self.assertFalse(health["providers"][0]["fresh"])
        self.assertFalse(health["providers"][0]["operational"])

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
        payload = [{"icaoId": "KJFK", "lat": 40.64, "lon": -73.78, "reportTime": _now()}]
        adapter = AviationWeatherAdapter(opener=lambda request, timeout: _Response(payload))
        rows = adapter.observations(adapter.fetch())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].provider, "aviationweather-gov")
        self.assertFalse(rows[0].provenance["key_required"])

    def test_aisstream_reads_secret_only_from_environment(self):
        socket = _Socket([
            {
                "MessageType": "PositionReport",
                "MetaData": {"MMSI": 123456789, "latitude": 10.0, "longitude": 20.0, "time_utc": _now()},
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

    def test_missing_ais_key_records_failed_run_and_never_qualifies(self):
        with patch.dict(os.environ, {}, clear=True):
            coordinator = TransportProviderCoordinator(TransportStore(":memory:"))
            result = coordinator.run_maritime(max_messages=1)
        self.assertFalse(result.successful)
        self.assertIn("not configured", result.error)
        self.assertEqual(len(coordinator.store.provider_runs("maritime", "aisstream")), 1)
        self.assertFalse(coordinator.store.coverage()["fully_qualified"])

    def test_worker_persists_heartbeat_and_stops_cleanly(self):
        store = TransportStore(":memory:")
        coordinator = _Coordinator(successful=True)
        worker = TransportOperationalWorker(
            store,
            coordinator=coordinator,
            provider="maritime",
            clock=lambda: 0.0,
            sleeper=lambda seconds: None,
        )
        code = worker.run(once=True)
        self.assertEqual(code, 0)
        self.assertEqual(coordinator.calls, ["aisstream"])
        health = store.workers()[0]
        self.assertEqual(health["state"], "STOPPED")
        self.assertEqual(health["cycles"], 1)
        self.assertEqual(health["failures"], 0)

    def test_worker_marks_failed_cycle_degraded(self):
        store = TransportStore(":memory:")
        worker = TransportOperationalWorker(
            store,
            coordinator=_Coordinator(successful=False),
            provider="aviation",
            clock=lambda: 0.0,
            sleeper=lambda seconds: None,
        )
        result = worker.run_cycle(force=True)
        self.assertEqual(result["state"], "DEGRADED")
        self.assertEqual(store.workers()[0]["failures"], 1)


if __name__ == "__main__":
    unittest.main()
