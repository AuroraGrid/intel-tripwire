from __future__ import annotations

import unittest

from phase33_complete import Phase33Application
from phase33_webcams import REGIONS, WebcamRegistry
from phase32_complete import Phase32Application


class Phase33RegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = WebcamRegistry()

    def payload(self, region="Asia", suffix="1"):
        return {
            "region": region,
            "country": "Pakistan",
            "city": "Islamabad",
            "title": f"City camera {suffix}",
            "source_type": "youtube",
            "source_url": f"https://www.youtube.com/watch?v=test{suffix}",
            "embed_url": f"https://www.youtube.com/embed/test{suffix}",
            "latitude": 33.6844,
            "longitude": 73.0479,
            "provider": "Example provider",
            "attribution": "Provider attribution required",
            "license_note": "Embedding subject to provider terms",
        }

    def test_release_is_forward_compatible(self):
        self.assertTrue(issubclass(Phase33Application, Phase32Application))

    def test_registration_does_not_claim_live(self):
        item = self.registry.register(self.payload())
        self.assertEqual(item["health"], "UNKNOWN")
        coverage = self.registry.coverage()
        asia = next(row for row in coverage["regions"] if row["region"] == "Asia")
        self.assertEqual(asia["registered"], 1)
        self.assertEqual(asia["online"], 0)
        self.assertFalse(asia["qualified"])

    def test_successful_observation_qualifies_camera(self):
        item = self.registry.register(self.payload())
        observed = self.registry.observe(item["webcam_id"], {"health": "ONLINE"})
        self.assertEqual(observed["health"], "ONLINE")
        self.assertTrue(observed["last_success_at"])
        self.assertEqual(observed["consecutive_failures"], 0)

    def test_three_degraded_observations_force_offline(self):
        item = self.registry.register(self.payload())
        webcam_id = item["webcam_id"]
        self.registry.observe(webcam_id, {"health": "DEGRADED"})
        self.registry.observe(webcam_id, {"health": "DEGRADED"})
        observed = self.registry.observe(webcam_id, {"health": "DEGRADED"})
        self.assertEqual(observed["health"], "OFFLINE")
        self.assertEqual(observed["consecutive_failures"], 3)

    def test_ten_online_cameras_qualify_one_region(self):
        for index in range(10):
            item = self.registry.register(self.payload("Europe", str(index)))
            self.registry.observe(item["webcam_id"], {"health": "ONLINE"})
        coverage = self.registry.coverage()
        europe = next(row for row in coverage["regions"] if row["region"] == "Europe")
        self.assertEqual(europe["online"], 10)
        self.assertTrue(europe["qualified"])
        self.assertEqual(coverage["qualified_regions"], 1)
        self.assertFalse(coverage["fully_qualified"])

    def test_all_required_regions_are_present(self):
        self.assertEqual(tuple(row["region"] for row in self.registry.coverage()["regions"]), REGIONS)

    def test_invalid_registration_is_rejected(self):
        payload = self.payload()
        payload["region"] = "Antarctica"
        with self.assertRaises(ValueError):
            self.registry.register(payload)
        payload = self.payload()
        payload["source_url"] = "javascript:alert(1)"
        with self.assertRaises(ValueError):
            self.registry.register(payload)

    def test_idempotent_source_registration(self):
        first = self.registry.register(self.payload())
        second_payload = self.payload()
        second_payload["title"] = "Updated title"
        second = self.registry.register(second_payload)
        self.assertEqual(first["webcam_id"], second["webcam_id"])
        self.assertEqual(second["title"], "Updated title")
        self.assertEqual(len(self.registry.list()), 1)


if __name__ == "__main__":
    unittest.main()
