import tempfile
import unittest
import uuid
from pathlib import Path

from phase15_mesh import SensorMesh
from storage import Store


class Phase15Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "phase15.db")
        _, token = self.store.create_user(f"admin-{uuid.uuid4().hex}@example.com", "admin")
        self.actor = self.store.auth(token)
        self.mesh = SensorMesh(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def test_default_registry_spans_operational_domains(self):
        sensors = self.mesh.sensors(self.actor)
        domains = {sensor["domain"] for sensor in sensors}
        self.assertGreaterEqual(len(sensors), 12)
        self.assertTrue({"aviation", "maritime", "internet", "wildfire", "weather", "cyber", "sanctions", "markets"}.issubset(domains))

    def test_health_updates_coverage(self):
        result = self.mesh.record_health(self.actor, "seismic.usgs", {"status": "live", "latency_ms": 80, "records_seen": 25})
        self.assertEqual(result["sensors"][0]["health"]["status"], "live")
        coverage = self.mesh.coverage(self.actor)
        self.assertEqual(coverage["phase"], 15)
        self.assertGreaterEqual(coverage["live"], 1)

    def test_observation_deduplication(self):
        observation = {"external_id": "quake-1", "title": "Magnitude 6.1 earthquake", "observed_at": "2026-07-17T10:00:00Z", "latitude": 35.1, "longitude": 70.2}
        first = self.mesh.ingest_observations(self.actor, "seismic.usgs", [observation])
        second = self.mesh.ingest_observations(self.actor, "seismic.usgs", [observation])
        self.assertEqual(first["accepted"], 1)
        self.assertEqual(second["duplicates_suppressed"], 1)
        self.assertEqual(len(self.mesh.observations(self.actor, "seismic")), 1)

    def test_custom_sensor_registration(self):
        sensor = self.mesh.register(self.actor, {"id": "internet.ripe", "domain": "internet", "provider": "RIPE Atlas", "transport": "https", "authority": "sensor_network", "enabled": True})
        self.assertEqual(sensor["provider"], "RIPE Atlas")
        self.assertTrue(sensor["enabled"])


if __name__ == "__main__":
    unittest.main()
