import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app
from phase8_runtime import OperationalAggregator, enrich_incident, operational_status, read_runtime
from release_engine import Adapter


class Phase8Tests(unittest.TestCase):
    def test_operational_status_detects_live_and_stale(self):
        snapshot = {
            "status": "ok",
            "generated_at": "2026-07-16T00:00:00Z",
            "mode": "live",
            "event_count": 4,
            "evidence_count": 8,
            "duplicates_suppressed": 2,
            "sources": [
                {"source": "A", "status": "online"},
                {"source": "B", "status": "degraded"},
            ],
        }
        current = datetime(2026, 7, 16, 0, 5, tzinfo=timezone.utc)
        live = operational_status(snapshot, 600, current)
        stale = operational_status(snapshot, 120, current)
        self.assertEqual(live["state"], "degraded")
        self.assertEqual(live["sources_online"], 1)
        self.assertEqual(stale["state"], "stale")
        self.assertTrue(stale["stale"])

    def test_enrich_incident_flattens_payload_and_evidence(self):
        incident = enrich_incident({
            "status": "SUPPORTED",
            "grade": "G3",
            "confidence": 91,
            "action": "ESCALATE",
            "payload": json.dumps({"what_changed": "Verified change", "independent_origins": 3}),
            "evidence": [{"source_family": "usgs.gov", "payload": json.dumps({"reliability_score": 92})}],
        })
        self.assertEqual(incident["k_align_status"], "SUPPORTED")
        self.assertEqual(incident["what_changed"], "Verified change")
        self.assertEqual(incident["evidence"][0]["reliability_score"], 92)

    def test_operational_aggregator_writes_runtime_snapshot(self):
        record = app.Evidence("e1", "Major earthquake reported", "https://example.test/e1", "USGS", "usgs.gov", 1, "2026-07-16T00:00:00Z", "sensor_observation", True)
        adapter = Adapter("USGS", "usgs.gov", 1, True, "earthquakes", lambda: [record])
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "runtime.json"
            aggregator = OperationalAggregator(adapter_factory=lambda query: [adapter], runtime_path=target)
            with patch.dict("os.environ", {"AURORA_OFFLINE": "0"}, clear=False):
                payload = aggregator.collect(force=True)
            snapshot = read_runtime(target)
        self.assertEqual(payload["mode"], "live")
        self.assertEqual(snapshot["status"], "ok")
        self.assertEqual(snapshot["sources"][0]["source"], "USGS")


if __name__ == "__main__":
    unittest.main()
