import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phase9_breadth_qualification import qualify as phase9_qualify
from phase9_scale import catalog_summary, curated_streams, scaled_registry_manifest, scheduled_providers
from phase10_assets import all_static_assets, static_counts
from phase10_release_qualification import qualify as phase10_qualify


class PhaseNineTenReleaseGateTests(unittest.TestCase):
    def test_phase9_registry_exceeds_public_baseline(self):
        summary = catalog_summary()
        providers = {str(item.get("provider") or item.get("name")) for item in scaled_registry_manifest()}
        self.assertGreater(len(providers), 65)
        self.assertGreater(summary["curated_streams"], 500)
        self.assertGreaterEqual(summary["layers"], 60)
        self.assertGreaterEqual(summary["capability_classes"], 15)
        self.assertEqual(len(curated_streams()), len(set(item["id"] for item in curated_streams())))

    def test_phase9_rotating_batch_is_bounded_and_deterministic(self):
        first = scheduled_providers(batch_size=10, slot=7)
        second = scheduled_providers(batch_size=10, slot=7)
        self.assertEqual([item.name for item in first], [item.name for item in second])
        self.assertEqual(len(first), 10)

    def test_phase9_offline_qualification_is_registry_only(self):
        result = phase9_qualify(run_live=False)
        self.assertTrue(result["registry_gate_passed"])
        self.assertTrue(result["passed"])

    def test_phase10_static_catalog_exceeds_fixed_reference_layers(self):
        counts = static_counts()
        self.assertGreater(counts.get("chokepoint", 0), 13)
        self.assertGreater(counts.get("hotspot", 0), 29)
        self.assertGreater(counts.get("market", 0), 92)
        self.assertEqual(len(all_static_assets()), sum(counts.values()))

    @patch("phase10_release_qualification.world_bank_countries", return_value=[{"id": str(i)} for i in range(196)])
    @patch("phase10_release_qualification.country_features", return_value={"features": [{} for _ in range(180)]})
    def test_phase10_offline_gate_covers_static_and_performance(self, _geometry, _countries):
        result = phase10_qualify(run_live=False)
        self.assertTrue(result["gates"]["countries"])
        self.assertTrue(result["gates"]["markets"])
        self.assertTrue(result["gates"]["webgl_and_filtering"])
        self.assertTrue(result["passed"])


if __name__ == "__main__":
    unittest.main()
