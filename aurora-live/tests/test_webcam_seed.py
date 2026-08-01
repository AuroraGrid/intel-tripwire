from __future__ import annotations

import json
import unittest
from pathlib import Path

from phase37_webcams import DurableWebcamRegistry, WebcamStore

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "fixtures" / "webcam_seed_manifest.json"
REGIONS = [
    "Oceania",
    "Africa",
    "Asia",
    "Middle East",
    "Europe",
    "North America",
    "South America",
]


class WebcamSeedTests(unittest.TestCase):
    def test_manifest_has_seventy_cameras_across_regions(self):
        rows = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(len(rows), 70)
        counts = {region: 0 for region in REGIONS}
        for row in rows:
            self.assertIn(row["region"], REGIONS)
            for key in (
                "country",
                "city",
                "title",
                "source_type",
                "source_url",
                "latitude",
                "longitude",
                "provider",
                "attribution",
                "license_note",
            ):
                self.assertTrue(str(row.get(key) or "").strip(), msg=f"missing {key}")
            counts[row["region"]] += 1
        self.assertEqual(counts, {region: 10 for region in REGIONS})

    def test_seed_registers_without_claiming_live(self):
        rows = json.loads(MANIFEST.read_text(encoding="utf-8"))
        store = WebcamStore(":memory:")
        registry = DurableWebcamRegistry(store)
        for row in rows:
            registry.register(row)
        coverage = registry.coverage()
        self.assertEqual(coverage["total_registered"], 70)
        self.assertEqual(coverage["total_online"], 0)
        self.assertFalse(coverage["fully_qualified"])
        self.assertEqual(coverage["qualified_regions"], 0)
        matrix = registry.matrix()
        self.assertEqual(matrix["target_slots"], 70)
        self.assertEqual(matrix["assigned_slots"], 70)
        self.assertEqual(matrix["qualified_slots"], 0)


if __name__ == "__main__":
    unittest.main()
