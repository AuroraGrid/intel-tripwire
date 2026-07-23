import tempfile
import unittest
import uuid
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phase14_integrity import EvidenceIntegrity
from phase15_mesh import SensorMesh
from phase16_synchronized import DetectionEngine
from phase17_fabric import RealtimeFabric
from storage import Store


class Phase17Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "phase17.db")
        _, token = self.store.create_user(f"admin-{uuid.uuid4().hex}@example.com", "admin")
        self.actor = self.store.auth(token)
        self.mesh = SensorMesh(self.store)
        self.integrity = EvidenceIntegrity(self.store)
        self.detection = DetectionEngine(self.store, self.mesh, self.integrity)
        self.fabric = RealtimeFabric(self.store, self.detection)

    def tearDown(self):
        self.temp.cleanup()

    def test_process_publishes_detection_event(self):
        self.mesh.ingest_observations(self.actor, "seismic.usgs", [{"external_id": "q1", "title": "Magnitude 6 earthquake", "observed_at": "2026-07-22T10:00:00Z", "latitude": 30, "longitude": 70}])
        result = self.fabric.process_pending(self.actor)
        self.assertEqual(result["processed"], 1)
        stream = self.fabric.stream(self.actor)
        self.assertEqual(len(stream["events"]), 1)
        self.assertEqual(stream["events"][0]["resource_type"], "detection")

    def test_checkpoint_and_replay(self):
        first = self.fabric.publish(self.actor, "test.one", "test", "1", {"value": 1})
        self.fabric.publish(self.actor, "test.two", "test", "2", {"value": 2})
        checkpoint = self.fabric.checkpoint(self.actor, "consumer-a", first["sequence"])
        self.assertEqual(checkpoint["last_sequence"], first["sequence"])
        replay = self.fabric.replay(self.actor, "consumer-a")
        self.assertEqual(len(replay["events"]), 1)
        self.assertEqual(replay["events"][0]["event_type"], "test.two")

    def test_processing_is_idempotent(self):
        self.mesh.ingest_observations(self.actor, "seismic.usgs", [{"external_id": "q2", "title": "Magnitude 5 earthquake", "observed_at": "2026-07-22T11:00:00Z"}])
        first = self.fabric.process_pending(self.actor)
        second = self.fabric.process_pending(self.actor)
        self.assertEqual(first["processed"], 1)
        self.assertEqual(second["processed"], 0)
        self.assertEqual(len(self.fabric.stream(self.actor)["events"]), 1)


if __name__ == "__main__":
    unittest.main()
