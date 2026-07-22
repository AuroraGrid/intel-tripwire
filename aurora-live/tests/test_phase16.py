import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phase14_integrity import EvidenceIntegrity
from phase15_mesh import SensorMesh
from phase16_detection import DetectionEngine
from storage import Store


class Phase16Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "phase16.db")
        _, token = self.store.create_user(f"admin-{uuid.uuid4().hex}@example.com", "admin")
        self.actor = self.store.auth(token)
        self.mesh = SensorMesh(self.store)
        self.integrity = EvidenceIntegrity(self.store)
        self.engine = DetectionEngine(self.store, self.mesh, self.integrity)
        self.mesh.register(self.actor, {
            "id": "seismic.partner",
            "domain": "seismic",
            "provider": "Partner Seismic Network",
            "authority": "sensor_network",
            "enabled": True,
        })

    def tearDown(self):
        self.temp.cleanup()

    def ingest(self, sensor_id, payload):
        result = self.mesh.ingest_observations(self.actor, sensor_id, [payload])
        self.assertEqual(result["accepted"], 1)
        return self.mesh.observations(self.actor, "seismic", 100)[0]["id"]

    def test_pending_observation_creates_detection_and_claim(self):
        self.ingest("seismic.usgs", {
            "external_id": "quake-1",
            "title": "Magnitude 6.1 earthquake near Kabul",
            "observed_at": "2026-07-20T10:00:00Z",
            "latitude": 34.5,
            "longitude": 69.2,
        })
        summary = self.engine.process_pending(self.actor)
        self.assertEqual(summary["processed"], 1)
        self.assertEqual(summary["created"], 1)
        detection = self.engine.detections(self.actor)[0]
        full = self.engine.detection(self.actor, detection["id"])
        self.assertEqual(full["domain"], "seismic")
        self.assertEqual(len(full["observations"]), 1)
        self.assertTrue(full["claim_id"])
        self.assertEqual(full["claim"]["assessment"]["grade"], "G1")

    def test_independent_provider_corroboration_confirms_detection(self):
        self.ingest("seismic.usgs", {
            "external_id": "usgs-2",
            "title": "Magnitude 6.1 earthquake near Kabul",
            "observed_at": "2026-07-20T10:00:00Z",
            "latitude": 34.50,
            "longitude": 69.20,
        })
        self.engine.process_pending(self.actor)
        self.ingest("seismic.partner", {
            "external_id": "partner-2",
            "title": "Magnitude 6.1 earthquake detected near Kabul",
            "observed_at": "2026-07-20T10:03:00Z",
            "latitude": 34.51,
            "longitude": 69.21,
        })
        summary = self.engine.process_pending(self.actor)
        self.assertEqual(summary["linked"], 1)
        detection = self.engine.detections(self.actor)[0]
        self.assertEqual(detection["state"], "CONFIRMED")
        self.assertEqual(detection["source_families"], 2)
        self.assertGreaterEqual(detection["confidence"], 0.7)

    def test_same_provider_duplicate_does_not_inflate_support(self):
        self.ingest("seismic.usgs", {
            "external_id": "usgs-3",
            "title": "Magnitude 5.8 earthquake near Herat",
            "observed_at": "2026-07-20T11:00:00Z",
            "latitude": 34.3,
            "longitude": 62.2,
        })
        self.engine.process_pending(self.actor)
        self.ingest("seismic.usgs", {
            "external_id": "usgs-3-copy",
            "title": "Magnitude 5.8 earthquake near Herat",
            "observed_at": "2026-07-20T11:02:00Z",
            "latitude": 34.3,
            "longitude": 62.2,
        })
        summary = self.engine.process_pending(self.actor)
        self.assertEqual(summary["duplicates"], 1)
        detection = self.engine.detections(self.actor)[0]
        self.assertEqual(detection["state"], "OPEN")
        self.assertEqual(detection["source_families"], 1)
        self.assertEqual(detection["supporting_observations"], 1)

    def test_explicit_contradiction_disputes_detection(self):
        observation_id = self.ingest("seismic.usgs", {
            "external_id": "usgs-4",
            "title": "Magnitude 6.0 earthquake near Quetta",
            "observed_at": "2026-07-20T12:00:00Z",
            "latitude": 30.2,
            "longitude": 67.0,
        })
        first = self.engine.correlate(self.actor, observation_id)
        detection_id = first["detection"]["id"]
        self.ingest("seismic.partner", {
            "external_id": "partner-retraction-4",
            "title": "Magnitude 6.0 earthquake near Quetta",
            "observed_at": "2026-07-20T12:05:00Z",
            "latitude": 30.2,
            "longitude": 67.0,
            "detection_relation": "CONTRADICTS",
            "contradicts_detection_id": detection_id,
        })
        summary = self.engine.process_pending(self.actor)
        self.assertEqual(summary["contradictions"], 1)
        detection = self.engine.detection(self.actor, detection_id)
        self.assertEqual(detection["state"], "DISPUTED")
        self.assertEqual(detection["claim"]["status"], "DISPUTED")

    def test_processing_is_idempotent_and_review_is_audited(self):
        self.ingest("seismic.usgs", {
            "external_id": "usgs-5",
            "title": "Magnitude 5.5 earthquake near Peshawar",
            "observed_at": "2026-07-20T13:00:00Z",
            "latitude": 34.0,
            "longitude": 71.5,
        })
        first = self.engine.process_pending(self.actor)
        second = self.engine.process_pending(self.actor)
        self.assertEqual(first["processed"], 1)
        self.assertEqual(second["processed"], 0)
        detection_id = first["detection_ids"][0]
        reviewed = self.engine.review(self.actor, detection_id, "ESCALATED", "Requires immediate analyst attention")
        self.assertEqual(reviewed["effective_state"], "ESCALATED")
        self.assertTrue(any(item["to_state"] == "ESCALATED" for item in reviewed["revisions"]))

    def test_scorecard_reports_pending_and_relation_counts(self):
        self.ingest("seismic.usgs", {
            "external_id": "usgs-6",
            "title": "Magnitude 5.2 earthquake near Islamabad",
            "observed_at": "2026-07-20T14:00:00Z",
            "latitude": 33.7,
            "longitude": 73.1,
        })
        before = self.engine.scorecard(self.actor)
        self.assertEqual(before["pending_observations"], 1)
        self.engine.process_pending(self.actor)
        after = self.engine.scorecard(self.actor)
        self.assertEqual(after["phase"], 16)
        self.assertEqual(after["pending_observations"], 0)
        self.assertEqual(after["observation_relations"]["ORIGINATES"], 1)


if __name__ == "__main__":
    unittest.main()
