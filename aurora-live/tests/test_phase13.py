import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phase12_fusion import SignalFusion
from phase13_delivery import DeliveryLayer
from storage import Store


class Phase13Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "phase13.db")
        _, token = self.store.create_user(f"analyst-{uuid.uuid4().hex}@example.com", "analyst")
        self.actor = self.store.auth(token)
        self.fusion = SignalFusion(self.store)
        self.delivery = DeliveryLayer(self.store, self.fusion)

    def tearDown(self):
        self.temp.cleanup()

    def test_layer_snapshot_timeline_and_openapi(self):
        self.fusion.ingest(self.actor, {"provider": "weather", "signal_type": "weather", "external_id": "w1", "title": "Storm", "latitude": 31.5, "longitude": 74.3, "confidence": 0.9})
        layer = self.delivery.create_layer(self.actor, {"name": "Weather", "slug": "weather", "layer_type": "points", "source_types": ["weather"]})
        snapshot = self.delivery.build_snapshot(self.actor, layer["id"])
        self.assertEqual(snapshot["feature_count"], 1)
        self.assertEqual(snapshot["geojson"]["features"][0]["properties"]["provider"], "weather")
        timeline = self.delivery.timeline(self.actor)
        self.assertEqual(timeline["events"][0]["kind"], "signal")
        spec = self.delivery.openapi()
        self.assertEqual(spec["openapi"], "3.1.0")
        self.assertIn("/api/platform/map-layers", spec["paths"])


if __name__ == "__main__":
    unittest.main()
