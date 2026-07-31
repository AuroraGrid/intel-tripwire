from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import live_qualification
from phase9_final_repairs import FINAL_REPAIR_NAMES


def snapshot(*, degraded_status="error", include_repairs=True):
    sources = []
    repairs = sorted(FINAL_REPAIR_NAMES) if include_repairs else sorted(FINAL_REPAIR_NAMES)[:-1]
    for index, name in enumerate(repairs):
        sources.append({"source": name, "status": "online", "capability": f"repair_{index}"})
    for index in range(20):
        sources.append({"source": f"Official source {index}", "status": "online", "capability": f"capability_{index}"})
    sources.append({"source": "Temporary endpoint", "status": degraded_status, "capability": "public_health", "error": "temporary outage"})
    return {
        "status": "ok",
        "mode": "live_degraded",
        "sources": sources,
        "event_count": 100,
        "evidence_count": 120,
        "duplicates_suppressed": 3,
        "registry_totals": {"adapters": 100, "capability_classes": 37},
        "last_error": None,
    }


class LiveQualificationTests(unittest.TestCase):
    def qualify(self, value):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            live_qualification,
            "OperationalAggregator",
        ) as aggregator, patch.object(
            live_qualification,
            "read_runtime",
            return_value=value,
        ), patch.object(
            live_qualification,
            "operational_status",
            return_value={"stale": False, "age_seconds": 1},
        ), patch.object(
            live_qualification,
            "final_phase9_adapters",
            return_value=[MagicMock()] * 30,
        ):
            aggregator.return_value.collect.return_value = {"collected": True}
            return live_qualification.qualify(str(Path(directory) / "runtime.json"), retries=1, minimum_online=12, minimum_capabilities=8)

    def test_unrelated_degraded_source_does_not_fail_breadth_gate(self):
        result = self.qualify(snapshot())
        self.assertTrue(result["passed"])
        self.assertTrue(result["degraded_but_qualified"])
        self.assertTrue(result["source_repair_passed"])
        self.assertTrue(result["live_breadth_passed"])

    def test_offline_fallback_still_blocks_qualification(self):
        result = self.qualify(snapshot(degraded_status="offline_fallback"))
        self.assertFalse(result["passed"])
        self.assertTrue(result["offline_fallback_present"])

    def test_missing_required_repair_still_blocks_qualification(self):
        result = self.qualify(snapshot(include_repairs=False))
        self.assertFalse(result["passed"])
        self.assertFalse(result["source_repair_passed"])


if __name__ == "__main__":
    unittest.main()
