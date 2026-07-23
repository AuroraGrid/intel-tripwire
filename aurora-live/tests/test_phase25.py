import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phase11_store import ForecastLedger
from phase12_fusion import SignalFusion
from phase13_delivery import DeliveryLayer
from phase14_integrity import EvidenceIntegrity
from phase15_mesh import SensorMesh
from phase16_synchronized import DetectionEngine
from phase17_fabric import RealtimeFabric
from phase18_graph import EntityGraph
from phase19_verification import MultimodalVerification
from phase20_operating_picture import LiveOperatingPicture
from phase21_routes import RouteIntelligence
from phase22_forecasting import AutonomousForecastEngine
from phase23_experience import UnifiedAnalystExperience
from phase24_ecosystem import DeveloperEcosystem
from phase24_mcp import AuroraMCPServer
from phase25_qualification import IntegrationQualifier
from storage import Store


class Phase25Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "phase25.db")
        _, token = self.store.create_user(
            f"admin-{uuid.uuid4().hex}@example.com", "admin"
        )
        self.actor = self.store.auth(token)
        self.mesh = SensorMesh(self.store)
        self.integrity = EvidenceIntegrity(self.store)
        self.detection = DetectionEngine(
            self.store, self.mesh, self.integrity
        )
        self.fabric = RealtimeFabric(self.store, self.detection)
        self.graph = EntityGraph(self.store, self.detection, self.fabric)
        self.media = MultimodalVerification(self.store)
        self.picture = LiveOperatingPicture(self.store, self.mesh)
        self.routes = RouteIntelligence(self.store, self.picture)
        self.forecasts = ForecastLedger(self.store)
        self.autonomous = AutonomousForecastEngine(
            self.store, self.forecasts, self.detection, self.routes
        )
        self.command = UnifiedAnalystExperience(
            self.store,
            self.mesh,
            self.integrity,
            self.detection,
            self.picture,
            self.routes,
            self.autonomous,
        )
        self.developer = DeveloperEcosystem(self.store)
        self.mcp = AuroraMCPServer(
            self.command,
            self.detection,
            self.routes,
            self.autonomous,
            self.integrity,
            self.mesh,
            self.fabric,
        )
        self.delivery = DeliveryLayer(
            self.store, SignalFusion(self.store)
        )
        self.qualifier = IntegrationQualifier(
            self.store,
            {
                "mesh": self.mesh,
                "integrity": self.integrity,
                "detection": self.detection,
                "fabric": self.fabric,
                "graph": self.graph,
                "media": self.media,
                "picture": self.picture,
                "routes": self.routes,
                "forecasts": self.forecasts,
                "autonomous": self.autonomous,
                "command": self.command,
                "developer": self.developer,
                "mcp": self.mcp,
                "delivery": self.delivery,
            },
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_canonical_domain_model_separates_record_responsibility(self):
        model = self.qualifier.domain_model()
        names = [item["record"] for item in model["records"]]
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue({
            "Sensor", "Observation", "Detection", "Claim", "Evidence",
            "Incident", "Alert", "Case", "Forecast", "Scenario",
            "System Output", "Audit Event",
        }.issubset(names))
        self.assertIn(
            "Forecasts remain separate from verified outcomes.",
            model["rules"],
        )

    def test_dated_world_monitor_baseline_uses_official_sources(self):
        baseline = self.qualifier.baseline
        self.assertEqual(baseline["verified_at"], "2026-07-23")
        self.assertEqual(
            baseline["capabilities"]["map_layers"]["value"], 45
        )
        self.assertTrue(
            all(
                source.startswith(("https://www.worldmonitor.app/", "https://github.com/koala73/"))
                for source in baseline["sources"]
            )
        )

    def test_benchmark_is_honest_about_gaps_and_unknowns(self):
        report = self.qualifier.benchmark(self.actor)
        results = {
            row["capability"]: row["result"] for row in report["matrix"]
        }
        self.assertEqual(results["Documented API operations"], "BEHIND")
        self.assertEqual(
            results["Live monitoring breadth"], "NOT_VERIFIED"
        )
        self.assertEqual(
            results["Claim-level provenance and contradiction ledger"],
            "AHEAD",
        )
        self.assertFalse(report["summary"]["superiority_claim_allowed"])

    def test_qualification_run_persists_checks_and_external_unknowns(self):
        report = self.qualifier.run(self.actor)
        self.assertEqual(report["status"], "PASS")
        self.assertTrue(report["release_gate"]["integration_checks_pass"])
        self.assertEqual(
            report["release_gate"]["external_gates_pending"], 2
        )
        latest = self.qualifier.latest(self.actor)
        self.assertEqual(latest["run_id"], report["run_id"])
        self.assertEqual(len(self.qualifier.runs(self.actor)), 1)

    def test_end_to_end_observation_to_forecast_and_mcp_delivery(self):
        accepted = self.mesh.ingest_observations(
            self.actor,
            "seismic.usgs",
            [{
                "external_id": "phase25-e2e",
                "title": "Magnitude 6.4 earthquake near Kabul",
                "observed_at": "2026-07-23T02:00:00Z",
                "latitude": 34.5,
                "longitude": 69.2,
            }],
        )
        self.assertEqual(accepted["accepted"], 1)
        fabric = self.fabric.process_pending(self.actor)
        self.assertEqual(fabric["events_published"], 1)
        graph = self.graph.process_fabric(self.actor)
        self.assertEqual(graph["processed"], 1)

        detection = self.detection.detections(self.actor)[0]
        full = self.detection.detection(self.actor, detection["id"])
        self.assertTrue(full["claim_id"])

        candidate = self.autonomous.propose(
            self.actor, "DETECTION", detection["id"]
        )
        approved = self.autonomous.approve(
            self.actor,
            candidate["id"],
            "Phase 25 end-to-end analyst approval",
        )
        self.assertTrue(approved["forecast_id"])
        forecast = self.forecasts.get(
            self.actor, approved["forecast_id"]
        )
        self.assertEqual(forecast["status"], "open")

        search = self.mcp.call(
            self.actor, "search_intelligence", {"query": "kabul"}
        )
        types = {item["type"] for item in search["results"]}
        self.assertTrue({"DETECTION", "CLAIM"}.issubset(types))

    def test_qualification_history_is_workspace_isolated(self):
        self.qualifier.run(self.actor)
        other = dict(self.actor)
        other["workspace_id"] = "other-workspace"
        latest = self.qualifier.latest(other)
        self.assertEqual(latest["status"], "NOT_RUN")
        self.assertEqual(self.qualifier.runs(other), [])


if __name__ == "__main__":
    unittest.main()
