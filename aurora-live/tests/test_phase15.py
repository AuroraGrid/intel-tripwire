import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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
        self.assertTrue(all(sensor.get("internal_id") for sensor in sensors))

    def test_health_updates_coverage(self):
        result = self.mesh.record_health(self.actor, "seismic.usgs", {"status": "live", "latency_ms": 80, "records_seen": 25})
        self.assertEqual(result["sensors"][0]["health"]["status"], "live")
        coverage = self.mesh.coverage(self.actor)
        self.assertEqual(coverage["phase"], 15)
        self.assertGreaterEqual(coverage["live"], 1)

    def test_health_validation(self):
        with self.assertRaises(ValueError):
            self.mesh.record_health(self.actor, "seismic.usgs", {"status": "broken"})
        with self.assertRaises(ValueError):
            self.mesh.record_health(self.actor, "seismic.usgs", {"status": "live", "error_rate": 1.5})

    def test_observation_deduplication(self):
        observation = {"external_id": "quake-1", "title": "Magnitude 6.1 earthquake", "observed_at": "2026-07-17T10:00:00Z", "latitude": 35.1, "longitude": 70.2}
        first = self.mesh.ingest_observations(self.actor, "seismic.usgs", [observation])
        second = self.mesh.ingest_observations(self.actor, "seismic.usgs", [observation])
        self.assertEqual(first["accepted"], 1)
        self.assertEqual(second["duplicates_suppressed"], 1)
        self.assertEqual(len(self.mesh.observations(self.actor, "seismic")), 1)

    def test_invalid_observations_are_rejected(self):
        result = self.mesh.ingest_observations(self.actor, "seismic.usgs", [
            {"title": ""},
            {"title": "Impossible coordinate", "latitude": 100, "longitude": 0},
            "not-an-object",
        ])
        self.assertEqual(result["accepted"], 0)
        self.assertEqual(result["rejected"], 3)

    def test_custom_sensor_registration_is_idempotent(self):
        payload = {"id": "internet.ripe", "domain": "internet", "provider": "RIPE Atlas", "transport": "https", "authority": "sensor_network", "enabled": True}
        first = self.mesh.register(self.actor, payload)
        second = self.mesh.register(self.actor, {**payload, "provider": "RIPE Atlas Network"})
        self.assertEqual(first["id"], "internet.ripe")
        self.assertEqual(second["provider"], "RIPE Atlas Network")
        self.assertTrue(second["enabled"])
        self.assertEqual(len([item for item in self.mesh.sensors(self.actor) if item["id"] == "internet.ripe"]), 1)


if __name__ == "__main__":
    unittest.main()
