from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from phase38_healthcheck import _append_summary, evaluate
from phase38_transport import TransportObservation, TransportRegistry, TransportStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _qualify(store: TransportStore, domain: str, provider: str) -> None:
    now = _now()
    registry = TransportRegistry(store)
    registry.register_provider({"provider": provider, "domain": domain})
    registry.observe_provider(provider, {"successful": True, "freshness_seconds": 1, "observed_at": now})
    store.record(
        TransportObservation(
            domain=domain,
            provider=provider,
            external_id=f"{provider}:observation",
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


class Phase38HealthcheckTests(unittest.TestCase):
    def test_gate_requires_both_operational_domains(self):
        store = TransportStore(":memory:")
        _qualify(store, "aviation", "aviation-fixture")
        result, passed = evaluate(store, max_age_seconds=900)
        self.assertFalse(passed)
        self.assertFalse(result["qualified"])
        self.assertEqual(result["operational_domains"], 1)

    def test_gate_passes_with_fresh_durable_aviation_and_maritime_data(self):
        store = TransportStore(":memory:")
        _qualify(store, "aviation", "aviation-fixture")
        _qualify(store, "maritime", "maritime-fixture")
        result, passed = evaluate(store, max_age_seconds=900)
        self.assertTrue(passed)
        self.assertTrue(result["qualified"])
        self.assertEqual(result["operational_domains"], 2)
        self.assertEqual(result["database_backend"], "sqlite")

    def test_gate_can_require_one_domain_for_targeted_operations(self):
        store = TransportStore(":memory:")
        _qualify(store, "maritime", "maritime-fixture")
        result, passed = evaluate(store, required_domains=("maritime",), max_age_seconds=900)
        self.assertTrue(passed)
        self.assertEqual(result["required_domains"], ["maritime"])

    def test_summary_contains_status_without_connection_material(self):
        store = TransportStore(":memory:")
        _qualify(store, "aviation", "aviation-fixture")
        _qualify(store, "maritime", "maritime-fixture")
        result, _ = evaluate(store, max_age_seconds=900)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "summary.md"
            _append_summary(str(target), result)
            text = target.read_text(encoding="utf-8")
        self.assertIn("AURORA transport production gate", text)
        self.assertIn("aviation-fixture", text)
        self.assertNotIn("postgresql://", text)


if __name__ == "__main__":
    unittest.main()
