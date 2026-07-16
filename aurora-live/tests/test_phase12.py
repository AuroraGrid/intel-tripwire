import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phase12_fusion import SignalFusion
from storage import Store


class Phase12Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "phase12.db")
        _, token = self.store.create_user(f"analyst-{uuid.uuid4().hex}@example.com", "analyst")
        self.actor = self.store.auth(token)
        self.fusion = SignalFusion(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def test_ingest_health_and_fusion(self):
        first = self.fusion.ingest(self.actor, {"provider": "ais", "signal_type": "maritime", "external_id": "v1", "title": "Vessel slowdown", "confidence": 0.8})
        second = self.fusion.ingest(self.actor, {"provider": "market", "signal_type": "market", "external_id": "m1", "title": "Freight spike", "confidence": 0.7})
        event = self.fusion.fuse(self.actor, {"signal_ids": [first["id"], second["id"]], "title": "Shipping disruption"})
        self.assertEqual(len(event["signals"]), 2)
        self.assertGreater(event["confidence"], 0.75)
        self.fusion.record_health(self.actor, {"provider": "ais", "ok": True, "latency_ms": 120, "records": 5})
        scorecard = self.fusion.provider_scorecard(self.actor)
        self.assertEqual(scorecard["providers"][0]["availability"], 1.0)


if __name__ == "__main__":
    unittest.main()
