import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phase11_store import ForecastLedger
from phase14_integrity import EvidenceIntegrity
from phase15_mesh import SensorMesh
from phase16_synchronized import DetectionEngine
from phase20_operating_picture import LiveOperatingPicture
from phase21_routes import RouteIntelligence
from phase22_forecasting import AutonomousForecastEngine
from storage import Store


class Phase22Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "phase22.db")
        _, token = self.store.create_user(
            f"admin-{uuid.uuid4().hex}@example.com", "admin"
        )
        self.actor = self.store.auth(token)
        self.mesh = SensorMesh(self.store)
        self.integrity = EvidenceIntegrity(self.store)
        self.detection = DetectionEngine(
            self.store, self.mesh, self.integrity
        )
        self.picture = LiveOperatingPicture(self.store, self.mesh)
        self.routes = RouteIntelligence(self.store, self.picture)
        self.forecasts = ForecastLedger(self.store)
        self.engine = AutonomousForecastEngine(
            self.store, self.forecasts, self.detection, self.routes
        )

    def tearDown(self):
        self.temp.cleanup()

    def create_detection(self, suffix="1"):
        result = self.mesh.ingest_observations(
            self.actor,
            "seismic.usgs",
            [{
                "external_id": f"quake-{suffix}",
                "title": f"Magnitude 6.{suffix} earthquake near Kabul",
                "observed_at": "2026-07-22T10:00:00Z",
                "latitude": 34.5,
                "longitude": 69.2,
            }],
        )
        self.assertEqual(result["accepted"], 1)
        processed = self.detection.process_pending(self.actor)
        self.assertEqual(processed["created"], 1)
        return self.detection.detections(self.actor)[0]

    def create_route(self):
        origin = self.routes.upsert_node(self.actor, {
            "node_type": "PORT",
            "name": "Origin Port",
            "country_code": "AA",
            "latitude": 0,
            "longitude": 0,
        })
        destination = self.routes.upsert_node(self.actor, {
            "node_type": "PORT",
            "name": "Destination Port",
            "country_code": "BB",
            "latitude": 0,
            "longitude": 2,
        })
        edge = self.routes.upsert_edge(self.actor, {
            "provider": "test-network",
            "external_id": "direct-route",
            "from_node_id": origin["id"],
            "to_node_id": destination["id"],
            "mode": "SEA",
            "transit_hours": 24,
            "base_cost": 100,
            "base_risk": 0.1,
            "bidirectional": False,
        })
        plan = self.routes.create_plan(self.actor, {
            "name": "Critical grain route",
            "origin_node_id": origin["id"],
            "destination_node_id": destination["id"],
            "commodity": "grain",
            "volume": 1000,
            "allowed_modes": ["SEA"],
        })
        return plan, edge

    def test_detection_candidate_requires_analyst_approval(self):
        detection = self.create_detection()
        candidate = self.engine.propose(
            self.actor, "DETECTION", detection["id"]
        )
        self.assertEqual(candidate["state"], "PROPOSED")
        self.assertIsNone(candidate["forecast_id"])
        self.assertGreater(candidate["probability"], 0)
        self.assertEqual(
            len(self.engine.revisions(self.actor, candidate["id"])), 1
        )

    def test_approval_creates_official_forecast_ledger_record(self):
        detection = self.create_detection("2")
        candidate = self.engine.propose(
            self.actor, "DETECTION", detection["id"]
        )
        approved = self.engine.approve(
            self.actor, candidate["id"], "Analyst verified the evidence chain"
        )
        self.assertEqual(approved["state"], "APPROVED")
        forecast = self.forecasts.get(
            self.actor, approved["forecast_id"]
        )
        self.assertEqual(
            forecast["latest_revision"]["source"],
            "phase22.deterministic",
        )
        self.assertIn("Analyst approval", forecast["latest_revision"]["rationale"])

    def test_suppress_and_reopen_are_append_only(self):
        detection = self.create_detection("3")
        candidate = self.engine.propose(
            self.actor, "DETECTION", detection["id"]
        )
        suppressed = self.engine.suppress(
            self.actor, candidate["id"], "Known exercise, not operational"
        )
        self.assertEqual(suppressed["state"], "SUPPRESSED")
        reopened = self.engine.reopen(
            self.actor, candidate["id"], "Exercise status no longer applies"
        )
        self.assertEqual(reopened["state"], "PROPOSED")
        actions = [
            row["action"]
            for row in self.engine.revisions(self.actor, candidate["id"])
        ]
        self.assertEqual(actions, ["PROPOSED", "SUPPRESSED", "REOPENED"])

    def test_workspace_base_rates_change_probability(self):
        detection = self.create_detection("4")
        before = self.engine.propose(
            self.actor, "DETECTION", detection["id"]
        )
        self.engine.set_base_rate(self.actor, {
            "domain": "seismic",
            "outcome_type": "MATERIAL_ESCALATION_7D",
            "probability": 0.8,
            "sample_size": 120,
            "evidence": {"method": "resolved historical cases"},
        })
        after = self.engine.propose(
            self.actor, "DETECTION", detection["id"]
        )
        self.assertGreater(after["probability"], before["probability"])

    def test_route_candidate_updates_approved_forecast_materially(self):
        plan, edge = self.create_route()
        candidate = self.engine.propose(
            self.actor, "ROUTE_PLAN", plan["id"]
        )
        approved = self.engine.approve(
            self.actor, candidate["id"], "Route owner accepted the forecast"
        )
        initial = self.forecasts.get(
            self.actor, approved["forecast_id"]
        )
        self.routes.record_disruption(self.actor, {
            "title": "Port access closure",
            "edge_id": edge["id"],
            "probability": 1.0,
            "impact": 1.0,
            "source_type": "detection",
            "source_id": "supported-closure",
            "evidence": {"claim_state": "SUPPORTED"},
        })
        self.routes.recalculate(
            self.actor, plan["id"], "Supported closure detection"
        )
        refreshed = self.engine.propose(
            self.actor, "ROUTE_PLAN", plan["id"]
        )
        final = self.forecasts.get(
            self.actor, refreshed["forecast_id"]
        )
        self.assertGreater(
            final["latest_revision"]["probability"],
            initial["latest_revision"]["probability"],
        )
        self.assertGreaterEqual(len(final["revisions"]), 2)

    def test_process_discovers_detection_and_route_candidates(self):
        self.create_detection("5")
        self.create_route()
        result = self.engine.process(self.actor)
        self.assertEqual(result["proposed"], 2)
        self.assertEqual(len(self.engine.candidates(self.actor)), 2)

    def test_workspace_isolation(self):
        detection = self.create_detection("6")
        candidate = self.engine.propose(
            self.actor, "DETECTION", detection["id"]
        )
        other = dict(self.actor)
        other["workspace_id"] = "other-workspace"
        with self.assertRaises(KeyError):
            self.engine.candidate(other, candidate["id"])
        self.assertEqual(self.engine.candidates(other), [])

    def test_scorecard_documents_policy(self):
        card = self.engine.scorecard(self.actor)
        self.assertEqual(card["phase"], 22)
        self.assertTrue(card["policy"]["analyst_approval_required"])
        self.assertFalse(card["policy"]["external_ai_required"])
        self.assertEqual(
            card["policy"]["minimum_material_probability_change"], 0.02
        )


if __name__ == "__main__":
    unittest.main()
