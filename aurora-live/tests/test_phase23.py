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
from phase23_experience import UnifiedAnalystExperience
from storage import Store


class Phase23Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "phase23.db")
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
        self.autonomous = AutonomousForecastEngine(
            self.store,
            self.forecasts,
            self.detection,
            self.routes,
        )
        self.experience = UnifiedAnalystExperience(
            self.store,
            self.mesh,
            self.integrity,
            self.detection,
            self.picture,
            self.routes,
            self.autonomous,
        )

    def tearDown(self):
        self.temp.cleanup()

    def create_detection(self):
        result = self.mesh.ingest_observations(
            self.actor,
            "seismic.usgs",
            [{
                "external_id": "phase23-quake",
                "title": "Magnitude 6.3 earthquake near Kabul",
                "observed_at": "2026-07-23T01:00:00Z",
                "latitude": 34.5,
                "longitude": 69.2,
            }],
        )
        self.assertEqual(result["accepted"], 1)
        self.detection.process_pending(self.actor)
        return self.detection.detections(self.actor)[0]

    def test_overview_unifies_operational_scorecards(self):
        detection = self.create_detection()
        card = self.experience.overview(self.actor)
        self.assertEqual(card["phase"], 23)
        self.assertEqual(card["queues"]["detections"], 1)
        self.assertEqual(
            card["priority"]["detections"][0]["id"], detection["id"]
        )
        self.assertIn("evidence_integrity", card["scorecards"])
        self.assertTrue(card["experience"]["unified_search"])

    def test_search_returns_detection_with_stable_type(self):
        detection = self.create_detection()
        results = self.experience.search(self.actor, "kabul")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["type"], "DETECTION")
        self.assertEqual(results[0]["id"], detection["id"])

    def test_saved_views_are_owner_scoped_and_shareable(self):
        private = self.experience.save_view(self.actor, {
            "name": "My escalation queue",
            "view_type": "detections",
            "configuration": {"state": "ESCALATED"},
        })
        colleague = dict(self.actor)
        colleague["id"] = "colleague-user"
        with self.assertRaises(KeyError):
            self.experience.saved_view(colleague, private["id"])

        shared = self.experience.save_view(self.actor, {
            "name": "Shared risk routes",
            "view_type": "routes",
            "configuration": {"minimum_risk": 70},
            "is_shared": True,
        })
        visible = self.experience.saved_view(colleague, shared["id"])
        self.assertTrue(visible["is_shared"])

    def test_assignments_comments_and_activity_are_append_only(self):
        detection = self.create_detection()
        assignment = self.experience.assign(self.actor, {
            "subject_type": "DETECTION",
            "subject_id": detection["id"],
            "assignee_user_id": self.actor["id"],
            "state": "IN_PROGRESS",
            "note": "Verify the independent source chain",
        })
        comment = self.experience.comment(self.actor, {
            "subject_type": "DETECTION",
            "subject_id": detection["id"],
            "body": "Primary sensor is healthy; seeking institutional confirmation.",
        })
        collaboration = self.experience.collaboration(
            self.actor, "DETECTION", detection["id"]
        )
        self.assertEqual(assignment["state"], "IN_PROGRESS")
        self.assertEqual(collaboration["comments"][0]["id"], comment["id"])
        actions = [item["action"] for item in self.experience.activity(self.actor)]
        self.assertIn("ASSIGNMENT_UPDATED", actions)
        self.assertIn("COMMENT_CREATED", actions)

    def test_workspace_isolation_covers_collaboration_and_views(self):
        detection = self.create_detection()
        self.experience.comment(self.actor, {
            "subject_type": "DETECTION",
            "subject_id": detection["id"],
            "body": "Workspace-scoped comment",
        })
        other = dict(self.actor)
        other["workspace_id"] = "other-workspace"
        collaboration = self.experience.collaboration(
            other, "DETECTION", detection["id"]
        )
        self.assertEqual(collaboration["comments"], [])
        self.assertIsNone(collaboration["assignment"])
        self.assertEqual(self.experience.activity(other), [])
        self.assertEqual(self.experience.saved_views(other), [])


if __name__ == "__main__":
    unittest.main()
