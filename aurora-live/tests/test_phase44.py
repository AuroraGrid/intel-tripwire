from __future__ import annotations

import unittest

from phase43_complete import Phase43Application
from phase44_benchmark import build_benchmark_report, gate
from phase44_complete import Phase44Application
from phase44_operations import OperationsHistoryStore, evaluate_redundancy


class Phase44Tests(unittest.TestCase):
    def test_release_is_forward_compatible(self):
        self.assertTrue(issubclass(Phase44Application, Phase43Application))

    def test_ops_history_summary(self):
        store = OperationsHistoryStore(":memory:")
        store.record(status="UP", uptime_ok=True, redundancy_ok=True, detail={"n": 1})
        store.record(status="UP", uptime_ok=True, redundancy_ok=False, detail={"n": 2})
        summary = store.summary()
        self.assertEqual(summary["samples"], 2)
        self.assertEqual(summary["uptime_ratio"], 1.0)
        self.assertEqual(summary["redundancy_ratio"], 0.5)

    def test_redundancy_modes(self):
        single = evaluate_redundancy(primary_ok=True, secondary_ok=None)
        dual = evaluate_redundancy(primary_ok=True, secondary_ok=False)
        self.assertEqual(single["mode"], "single")
        self.assertTrue(single["ok"])
        self.assertEqual(dual["mode"], "dual")
        self.assertFalse(dual["ok"])

    def test_benchmark_never_auto_promotes_ten_of_ten(self):
        report = build_benchmark_report(
            product={"phase": 44, "capabilities": [{"status": "LIVE"} for _ in range(80)]},
            transport_health={"providers": [{} for _ in range(70)]},
            infrastructure_health={"providers": [{} for _ in range(10)]},
            markets_health={"providers": [{} for _ in range(10)]},
            ops_summary={"samples": 200, "uptime_ratio": 1.0},
            baseline={"named_providers": 65, "map_layers": 56, "source": "fixture"},
        )
        self.assertFalse(report["ten_of_ten"])
        self.assertIn(report["overall"], {"VERIFIED", "PARTIAL", "NOT_VERIFIED"})
        self.assertEqual(gate(10, 5), "VERIFIED")
        self.assertEqual(gate(4, 5), "PARTIAL")


if __name__ == "__main__":
    unittest.main()
