from __future__ import annotations

import os
import struct
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from phase34_imagery import ImageRegistry
from phase35_complete import Phase35Application
from phase35_sources import HttpResponse, ImageCandidate, SourceAdapter, SourceError
from phase36_complete import Phase36Application
from phase36_operations import OperationalCoordinator, regional_baseline
from phase36_sources import BASELINE_REGION_ADAPTERS, JmaHimawariAdapter, operational_adapter_names
from phase36_store import OperationalStore


def png(width=64, height=32):
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + struct.pack(">II", width, height) + b"\x00" * 16


class FakeTransport:
    def __init__(self, *, fail=False):
        self.fail = fail
        self._telemetry = {}

    def clear(self):
        self._telemetry = {}

    def snapshot(self):
        return dict(self._telemetry)

    def get(self, url, *, allowed_hosts, max_bytes):
        del allowed_hosts, max_bytes
        if self.fail:
            raise SourceError("synthetic provider outage")
        self._telemetry[url] = {"status": 200}
        return HttpResponse(
            status=200,
            url=url,
            headers={"content-type": "image/png", "last-modified": "Mon, 27 Jul 2026 12:00:00 GMT"},
            body=png(),
        )

    def get_json(self, url, *, allowed_hosts, max_bytes=2_000_000):
        del url, allowed_hosts, max_bytes
        if self.fail:
            raise SourceError("synthetic provider outage")
        return [
            {
                "identifier": "20260727120000",
                "caption": "Earth",
                "image": "epic_1b_20260727120000",
                "version": "03",
                "date": "2026-07-27 12:00:00",
                "centroid_coordinates": {"lat": 12.5, "lon": 81.2},
            }
        ]


class FailingAdapter(SourceAdapter):
    name = "noaa-goes"

    def discover(self, transport):
        del transport
        return [
            ImageCandidate(
                adapter=self.name,
                external_id="failure",
                source_payload={
                    "region": "North America",
                    "country": "United States",
                    "title": "Failure fixture",
                    "category": "satellite",
                    "geographic_scope": "fixture",
                    "source_url": "https://example.gov/source",
                    "image_url": "https://example.gov/image.png",
                    "latitude": 39,
                    "longitude": -98,
                    "provider": "Fixture",
                    "attribution": "Fixture",
                    "license_note": "Fixture",
                    "refresh_interval_seconds": 300,
                    "max_age_seconds": 1800,
                },
                captured_at="2026-07-27T12:00:00Z",
                image_url="https://example.gov/image.png",
                allowed_hosts=("example.gov",),
                metadata={},
            )
        ]


class Phase36Tests(unittest.TestCase):
    def test_release_is_forward_compatible(self):
        self.assertTrue(issubclass(Phase36Application, Phase35Application))

    def test_baseline_registry_has_one_official_adapter_per_region(self):
        self.assertEqual(
            set(BASELINE_REGION_ADAPTERS),
            {"Oceania", "Africa", "Asia", "Middle East", "Europe", "North America", "South America"},
        )
        self.assertTrue(set(BASELINE_REGION_ADAPTERS.values()).issubset(set(operational_adapter_names())))

    def test_jma_slot_is_ten_minute_aligned_and_lagged(self):
        now = datetime(2026, 7, 28, 12, 37, tzinfo=timezone.utc)
        slot = JmaHimawariAdapter.latest_slot(now)
        self.assertEqual(slot, datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc))

    def test_fake_live_ingestion_qualifies_all_seven_regions(self):
        registry = ImageRegistry()
        store = OperationalStore(":memory:")
        coordinator = OperationalCoordinator(registry, store, transport=FakeTransport())
        result = coordinator.run_due(list(BASELINE_REGION_ADAPTERS.values()), force=True)
        self.assertEqual(result["failed"], 0)
        baseline = regional_baseline(registry, store)
        self.assertTrue(baseline["fully_qualified"], baseline)
        self.assertEqual(baseline["qualified_regions"], 7)
        self.assertEqual(len(store.observations(limit=20)), 7)

    def test_registration_without_observation_does_not_qualify(self):
        baseline = regional_baseline(ImageRegistry(), OperationalStore(":memory:"))
        self.assertFalse(baseline["fully_qualified"])
        self.assertEqual(baseline["qualified_regions"], 0)

    def test_circuit_breaker_opens_after_three_failures(self):
        store = OperationalStore(":memory:")
        coordinator = OperationalCoordinator(ImageRegistry(), store, transport=FakeTransport(fail=True))
        with patch("phase36_operations.build_operational_adapter", return_value=FailingAdapter()):
            for _ in range(3):
                result = coordinator.run_adapter("noaa-goes", force=True)
                self.assertEqual(result["status"], "FAILED")
            state = store.provider_state("noaa-goes")
            self.assertEqual(state["circuit_state"], "OPEN")
            blocked = coordinator.run_adapter("noaa-goes")
            self.assertEqual(blocked["status"], "CIRCUIT_OPEN")
            self.assertFalse(blocked["executed"])

    def test_sqlite_store_preserves_ticks_and_provider_telemetry(self):
        store = OperationalStore(":memory:")
        store.upsert_provider(
            {
                "adapter": "test",
                "circuit_state": "CLOSED",
                "consecutive_failures": 0,
                "last_status": "SUCCESS",
                "last_run_id": 1,
                "last_attempt_at": "2026-07-28T00:00:00Z",
                "last_success_at": "2026-07-28T00:00:00Z",
                "next_due_at": "2026-07-28T00:10:00Z",
                "next_attempt_at": "",
                "last_error": "",
                "telemetry_json": '{"example.gov":{"status":200}}',
            }
        )
        store.record_tick(
            {
                "started_at": "2026-07-28T00:00:00Z",
                "completed_at": "2026-07-28T00:00:01Z",
                "requested": 1,
                "executed": 1,
                "skipped": 0,
                "successful": 1,
                "failed": 0,
                "details": [{"adapter": "test", "status": "SUCCESS"}],
            }
        )
        self.assertEqual(store.provider_states()[0]["telemetry"]["example.gov"]["status"], 200)
        self.assertEqual(store.ticks()[0]["successful"], 1)

    @unittest.skipUnless(os.getenv("AURORA_PHASE36_POSTGRES_DSN"), "PostgreSQL DSN not configured")
    def test_postgres_operational_store_contract(self):
        store = OperationalStore(os.environ["AURORA_PHASE36_POSTGRES_DSN"])
        marker = f"phase36-test-{datetime.now(timezone.utc).timestamp()}"
        run_id = store.start_run(marker)
        store.finish_run(run_id, discovered=0, succeeded=0, failed=1, error="qualification")
        self.assertEqual(store.runs(1)[0]["adapter"], marker)


if __name__ == "__main__":
    unittest.main()
