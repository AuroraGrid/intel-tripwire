import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app
from phase9_engine import Phase9Aggregator, registry_summary
from phase9_sources import RUNTIME, SOURCES, SourceSpec, fetch_source, phase9_adapters
from release_engine import Adapter


class Phase9Tests(unittest.TestCase):
    def test_registry_meets_initial_phase9_breadth_gate(self):
        summary = registry_summary()
        self.assertGreaterEqual(summary["totals"]["adapters"], 25)
        self.assertGreaterEqual(summary["totals"]["capability_classes"], 8)
        self.assertGreaterEqual(summary["totals"]["providers"], 15)

    def test_phase9_adapters_include_existing_and_new_sources(self):
        names = {adapter.name for adapter in phase9_adapters(app.DEFAULT_QUERY)}
        self.assertIn("USGS", names)
        self.assertIn("NWS Active Alerts", names)
        self.assertIn("NVD Recent Vulnerabilities", names)

    def test_nws_parser_normalizes_official_alert(self):
        spec = next(source for source in SOURCES if source.name == "NWS Active Alerts")
        payload = {"features": [{"properties": {"headline": "Tornado Warning", "sent": "2026-07-16T00:00:00Z", "description": "Take shelter", "@id": "https://api.weather.gov/alerts/1", "sender": "NWS"}}]}
        with patch("phase9_sources._json", return_value=payload):
            records = fetch_source(spec)
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0].official)
        self.assertEqual(records[0].source_family, "weather.gov")

    def test_source_runtime_uses_cache(self):
        spec = SourceSpec("Cache Test", "Provider", "example.test", "test", "official_record", "global", 300, "test", "https://example.test/feed")
        record = app.Evidence("e1", "Test", "https://example.test/1", "Cache Test", "example.test", 1, "2026-07-16T00:00:00Z", "official_record", True)
        with patch("phase9_sources.fetch_source", return_value=[record]) as fetcher:
            first = RUNTIME.fetch(spec)
            second = RUNTIME.fetch(spec)
        self.assertEqual(first[0].id, second[0].id)
        self.assertEqual(fetcher.call_count, 1)

    def test_aggregator_publishes_registry_and_live_totals(self):
        record = app.Evidence("e1", "Official test alert", "https://example.test/1", "Test", "example.test", 1, "2026-07-16T00:00:00Z", "official_record", True)
        adapter = Adapter("Test", "example.test", 1, True, "test_capability", lambda: [record])
        aggregator = Phase9Aggregator(adapter_factory=lambda query: [adapter])
        with patch("phase9_engine.registry_manifest", return_value=[{"name": "Test", "provider": "Test Provider", "capability": "test_capability", "official": True, "tier": 1, "runtime": {}}]):
            payload = aggregator.collect(force=True)
        self.assertEqual(payload["registry_totals"]["providers"], 1)
        self.assertEqual(payload["live_totals"]["online_sources"], 1)
        self.assertIn("phase9_gate", payload)


if __name__ == "__main__":
    unittest.main()
