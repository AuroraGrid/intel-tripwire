from __future__ import annotations

import os
import unittest
import uuid

from phase39_infrastructure import InfrastructureObservation, ProviderRun, _now
from phase39_operational import OperationalInfrastructureStore


@unittest.skipUnless(os.getenv("AURORA_TEST_POSTGRES_URL"), "PostgreSQL test URL not configured")
class Phase39PostgresTests(unittest.TestCase):
    def setUp(self):
        self.store = OperationalInfrastructureStore(os.environ["AURORA_TEST_POSTGRES_URL"])

    def test_observation_provider_and_run_are_durable(self):
        suffix = uuid.uuid4().hex
        provider = f"phase39-postgres-{suffix}"
        external_id = f"event-{suffix}"
        now = _now()
        self.store.upsert_provider(
            {
                "provider": provider,
                "layer": "outage",
                "state": "ONLINE",
                "last_attempt_at": now,
                "last_success_at": now,
                "consecutive_failures": 0,
                "freshness_seconds": 0,
                "last_error": "",
                "completeness_note": "test",
                "license_note": "test",
            }
        )
        self.store.record(
            InfrastructureObservation(
                layer="outage",
                provider=provider,
                external_id=external_id,
                observed_at=now,
                event_time=now,
                severity="INFO",
                title="PostgreSQL fixture",
                summary="durability fixture",
                source_url="https://example.invalid",
                payload={"durable": True},
                provenance={"source": "test"},
            )
        )
        self.store.record_run(ProviderRun(provider, "outage", True, True, 1, started_at=now, completed_at=now))

        second = OperationalInfrastructureStore(os.environ["AURORA_TEST_POSTGRES_URL"])
        rows = second.observations(provider=provider)
        runs = second.runs(provider=provider)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["external_id"], external_id)
        self.assertTrue(rows[0]["payload"]["durable"])
        self.assertTrue(runs[0]["successful"])


if __name__ == "__main__":
    unittest.main()
