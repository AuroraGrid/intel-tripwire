from __future__ import annotations

import os
import unittest
import uuid

from phase40_markets import MarketObservation, MarketStore, ProviderRun, _now


@unittest.skipUnless(os.getenv("AURORA_TEST_POSTGRES_URL"), "PostgreSQL test URL not configured")
class Phase40PostgresTests(unittest.TestCase):
    def test_observation_provider_and_run_survive_restart(self):
        target = os.environ["AURORA_TEST_POSTGRES_URL"]
        store = MarketStore(target)
        suffix = uuid.uuid4().hex
        provider = f"phase40-postgres-{suffix}"
        instrument = f"instrument-{suffix}"
        external_id = f"observation-{suffix}"
        now = _now()
        store.upsert_provider(
            {
                "provider": provider,
                "domain": "fx",
                "state": "ONLINE",
                "last_attempt_at": now,
                "last_success_at": now,
                "consecutive_failures": 0,
                "event_age_seconds": 0,
                "last_error": "",
                "completeness_note": "test",
                "license_note": "test",
            }
        )
        store.record(
            MarketObservation(
                domain="fx",
                provider=provider,
                instrument=instrument,
                external_id=external_id,
                observed_at=now,
                event_time=now,
                value=1.25,
                unit="test",
                status="test",
                payload={"durable": True},
                provenance={"source": "test"},
            )
        )
        store.record_run(ProviderRun(provider, "fx", True, True, 1, started_at=now, completed_at=now))

        second = MarketStore(target)
        rows = second.observations(provider=provider)
        runs = second.runs(provider=provider)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["instrument"], instrument)
        self.assertTrue(rows[0]["payload"]["durable"])
        self.assertTrue(runs[0]["successful"])


if __name__ == "__main__":
    unittest.main()
