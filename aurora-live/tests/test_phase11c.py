import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phase11_operations import DecisionOperations
from phase11_outputs import SystemOutputStore
from phase11_store import ForecastLedger
from storage import Store


class Phase11CTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "phase11c.db")
        _, token = self.store.create_user(f"analyst-{uuid.uuid4().hex}@example.com", "analyst")
        self.actor = self.store.auth(token)
        self.forecasts = ForecastLedger(self.store)
        self.outputs = SystemOutputStore(self.store)
        self.ops = DecisionOperations(self.store, self.forecasts, self.outputs)

    def tearDown(self):
        self.temp.cleanup()

    def test_portfolio_and_hall_of_record_use_stored_data(self):
        forecast = self.forecasts.create(self.actor, {"question": "Will the gate pass?", "probability": 0.7})
        output = self.outputs.create(self.actor, {
            "module": "K_ALIGN", "subject_type": "forecast", "subject_id": forecast["id"],
            "forecast_id": forecast["id"], "summary": "Evidence supports the estimate",
            "payload": {"status": "SUPPORTED"}, "evidence_links": ["record:1"],
        })
        review = self.outputs.review(self.actor, output["id"], {"review_kind": "red_team", "decision": "affirm"})["reviews"][-1]
        portfolio = self.ops.portfolio(self.actor)
        record = self.ops.hall_of_record(self.actor, 500)
        self.assertTrue(any(row["id"] == forecast["id"] for row in portfolio["forecasts"]))
        self.assertGreaterEqual(portfolio["summary"]["module_outputs"]["K_ALIGN"], 1)
        ids = {row["id"] for row in record["records"]}
        self.assertTrue({forecast["id"], output["id"], review["id"]} <= ids)

    def test_scenario_branching_and_dependencies(self):
        forecast = self.forecasts.create(self.actor, {"question": "Will disruption occur?", "probability": 0.4})
        first = self.ops.create_scenario(self.actor, forecast["id"], {
            "name": "Base case", "probability": 0.4, "dependencies": ["asset:port"],
            "assumptions": ["traffic remains normal"], "decision_costs": {"wait": 5},
        })
        parent = first["nodes"][0]["id"]
        graph = self.ops.create_scenario(self.actor, forecast["id"], {
            "name": "Escalation", "probability": 0.2, "parent_id": parent,
            "dependencies": ["incident:test"], "decision_costs": {"hedge": 2},
        })
        self.assertEqual(len(graph["nodes"]), 2)
        self.assertTrue(any(edge["type"] == "branches_to" for edge in graph["edges"]))
        self.assertTrue(any(edge["type"] == "depends_on" for edge in graph["edges"]))

    def test_alert_precision_recall_and_latency(self):
        self.ops.record_alert_outcome(self.actor, {
            "outcome": "true_positive", "detected_at": "2026-01-01T00:00:00Z", "decided_at": "2026-01-01T00:02:00Z"
        })
        self.ops.record_alert_outcome(self.actor, {"outcome": "false_positive"})
        self.ops.record_alert_outcome(self.actor, {"outcome": "false_negative"})
        metrics = self.ops.performance(self.actor)
        self.assertEqual(metrics["precision"], 0.5)
        self.assertEqual(metrics["recall"], 0.5)
        self.assertEqual(metrics["false_alarm_rate"], 0.5)
        self.assertEqual(metrics["mean_time_to_decision_seconds"], 120.0)


if __name__ == "__main__":
    unittest.main()
