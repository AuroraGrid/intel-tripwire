import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phase11_metrics import brier_score, calibration_error, logarithmic_score
from phase11_store import ForecastLedger
from storage import Store


class Phase11Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "phase11.db")
        self.user, self.token = self.store.create_user("analyst@example.com", "analyst")
        self.actor = self.store.auth(self.token)
        self.ledger = ForecastLedger(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def test_known_metric_values(self):
        self.assertAlmostEqual(brier_score(0.8, True), 0.04)
        self.assertGreater(logarithmic_score(0.8, True), 0.0)
        self.assertAlmostEqual(calibration_error([
            {"probability": 0.8, "outcome": True},
            {"probability": 0.8, "outcome": False},
        ], bins=5), 0.3)

    def test_forecast_revision_and_resolution_history(self):
        created = self.ledger.create(self.actor, {
            "question": "Will the event occur by the horizon?",
            "horizon": "2026-12-31T23:59:59Z",
            "probability": 0.6,
            "confidence_low": 0.5,
            "confidence_high": 0.7,
            "source": "analyst",
            "trigger_map": [{"trigger": "threshold crossed"}],
            "falsifiers": ["deadline passes"],
        })
        self.ledger.revise(self.actor, created["id"], {"probability": 0.75, "source": "analyst", "rationale": "new evidence"})
        resolved = self.ledger.resolve(self.actor, created["id"], True, "verified outcome")
        self.assertEqual(resolved["status"], "resolved")
        self.assertEqual(len(resolved["revisions"]), 2)
        self.assertTrue(resolved["outcome"])
        metrics = self.ledger.metrics(self.actor)
        self.assertEqual(metrics["count"], 1)
        self.assertAlmostEqual(metrics["brier"], 0.0625)

    def test_invalid_interval_rejected(self):
        created = self.ledger.create(self.actor, {"question": "Test question"})
        with self.assertRaises(ValueError):
            self.ledger.revise(self.actor, created["id"], {"probability": 0.6, "confidence_low": 0.7, "confidence_high": 0.8})


if __name__ == "__main__":
    unittest.main()
