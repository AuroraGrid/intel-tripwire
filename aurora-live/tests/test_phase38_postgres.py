from __future__ import annotations

import os
import unittest
import uuid
from datetime import datetime, timezone

from phase38_transport import TransportObservation, TransportRegistry, TransportStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@unittest.skipUnless(os.getenv("AURORA_TEST_POSTGRES_URL"), "AURORA_TEST_POSTGRES_URL is not configured")
class Phase38PostgresTests(unittest.TestCase):
    def test_provider_run_observation_and_worker_health_are_durable(self):
        store = TransportStore(os.environ["AURORA_TEST_POSTGRES_URL"])
        suffix = uuid.uuid4().hex
        provider = f"maritime-postgres-{suffix}"
        worker = f"phase38-postgres-{suffix}"
        now = _now()

        registry = TransportRegistry(store)
        registry.register_provider(
            {
                "provider": provider,
                "domain": "maritime",
                "license_note": "test-only",
                "completeness_note": "test-only",
            }
        )
        store.record(
            TransportObservation(
                domain="maritime",
                provider=provider,
                external_id=f"ais:{suffix}",
                observed_at=now,
                event_time=now,
                latitude=10.0,
                longitude=20.0,
                state="FRESH",
                payload={"fixture": True},
                provenance={"source": "postgres-test"},
            )
        )
        registry.observe_provider(
            provider,
            {"successful": True, "observed_at": now, "freshness_seconds": 0},
        )
        store.record_provider_run(
            {
                "provider": provider,
                "domain": "maritime",
                "started_at": now,
                "completed_at": now,
                "successful": True,
                "observations": 1,
                "duration_ms": 5,
            }
        )
        store.upsert_worker(
            {
                "worker": worker,
                "state": "RUNNING",
                "started_at": now,
                "last_heartbeat_at": now,
                "last_cycle_at": now,
                "cycles": 1,
                "failures": 0,
                "last_error": "",
            }
        )

        reopened = TransportStore(os.environ["AURORA_TEST_POSTGRES_URL"])
        observations = reopened.observations("maritime", provider)
        runs = reopened.provider_runs("maritime", provider)
        workers = reopened.workers()
        health = reopened.health(max_age_seconds=300)
        provider_health = next(row for row in health["providers"] if row["provider"] == provider)

        self.assertEqual(len(observations), 1)
        self.assertEqual(len(runs), 1)
        self.assertTrue(runs[0]["successful"])
        self.assertTrue(provider_health["operational"])
        self.assertTrue(any(row["worker"] == worker and row["state"] == "RUNNING" for row in workers))


if __name__ == "__main__":
    unittest.main()
