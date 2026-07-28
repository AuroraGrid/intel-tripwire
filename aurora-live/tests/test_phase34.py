from __future__ import annotations

import hashlib
import unittest
from datetime import datetime, timedelta, timezone

from phase33_complete import Phase33Application
from phase34_complete import Phase34Application
from phase34_imagery import IMAGE_CATEGORIES, ImageRegistry


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class Phase34ImageryTests(unittest.TestCase):
    def setUp(self):
        self.registry = ImageRegistry()
        self.now = datetime.now(timezone.utc).replace(microsecond=0)

    def payload(self, region="Asia", suffix="1", category="satellite"):
        return {
            "region": region,
            "country": "Pakistan",
            "title": f"Regional image {suffix}",
            "category": category,
            "geographic_scope": "Northern Pakistan and adjoining areas",
            "source_url": f"https://example.test/source/{suffix}",
            "image_url": f"https://example.test/images/{suffix}.jpg",
            "latitude": 33.6844,
            "longitude": 73.0479,
            "provider": "Example provider",
            "attribution": "Provider attribution required",
            "license_note": "Display subject to provider terms",
            "refresh_interval_seconds": 300,
            "max_age_seconds": 1800,
        }

    def observation(self, suffix="1", age_seconds=60, state="FRESH"):
        body = {
            "state": state,
            "observed_at": iso(self.now),
        }
        if state in {"FRESH", "STALE"}:
            body.update(
                {
                    "captured_at": iso(self.now - timedelta(seconds=age_seconds)),
                    "content_sha256": hashlib.sha256(f"image-{suffix}".encode()).hexdigest(),
                    "content_type": "image/jpeg",
                    "byte_length": 150000,
                    "width": 1920,
                    "height": 1080,
                }
            )
        return body

    def test_release_is_forward_compatible(self):
        self.assertTrue(issubclass(Phase34Application, Phase33Application))

    def test_registration_is_not_current_evidence(self):
        item = self.registry.register(self.payload())
        self.assertEqual(item["state"], "UNKNOWN")
        self.assertFalse(self.registry.coverage()["fully_qualified"])
        self.assertEqual(self.registry.latest(), [])

    def test_fresh_observation_qualifies_image(self):
        item = self.registry.register(self.payload())
        observed = self.registry.observe(item["source_id"], self.observation())
        self.assertEqual(observed["state"], "FRESH")
        self.assertTrue(observed["last_captured_at"])
        self.assertTrue(observed["last_changed_at"])
        self.assertEqual(observed["consecutive_failures"], 0)
        self.assertEqual(len(self.registry.latest()), 1)

    def test_old_capture_is_forced_stale(self):
        item = self.registry.register(self.payload())
        observed = self.registry.observe(item["source_id"], self.observation(age_seconds=3600))
        self.assertEqual(observed["state"], "STALE")
        self.assertEqual(self.registry.latest(), [])

    def test_replayed_content_is_forced_stale(self):
        item = self.registry.register(self.payload())
        source_id = item["source_id"]
        first = self.observation()
        self.registry.observe(source_id, first)
        later = dict(first)
        later["observed_at"] = iso(self.now + timedelta(seconds=1900))
        later["captured_at"] = iso(self.now + timedelta(seconds=1850))
        observed = self.registry.observe(source_id, later)
        self.assertEqual(observed["state"], "STALE")
        self.assertEqual(observed["stale_cycles"], 1)

    def test_duplicate_content_is_linked(self):
        first = self.registry.register(self.payload("Europe", "a", "weather"))
        second = self.registry.register(self.payload("Europe", "b", "weather"))
        observation = self.observation("shared")
        self.registry.observe(first["source_id"], observation)
        duplicate = self.registry.observe(second["source_id"], observation)
        self.assertEqual(duplicate["duplicate_of"], first["source_id"])
        self.assertEqual(self.registry.source_health()["duplicate_sources"], 1)

    def test_three_degraded_observations_force_offline(self):
        item = self.registry.register(self.payload())
        source_id = item["source_id"]
        self.registry.observe(source_id, self.observation(state="DEGRADED"))
        self.registry.observe(source_id, self.observation(state="DEGRADED"))
        observed = self.registry.observe(source_id, self.observation(state="DEGRADED"))
        self.assertEqual(observed["state"], "OFFLINE")
        self.assertEqual(observed["consecutive_failures"], 3)

    def test_one_fresh_source_per_region_completes_baseline_matrix(self):
        regions = ("Oceania", "Africa", "Asia", "Middle East", "Europe", "North America", "South America")
        for index, region in enumerate(regions):
            item = self.registry.register(self.payload(region, str(index), IMAGE_CATEGORIES[index]))
            self.registry.observe(item["source_id"], self.observation(str(index)))
        coverage = self.registry.coverage()
        self.assertEqual(coverage["qualified_regions"], 7)
        self.assertTrue(coverage["fully_qualified"])
        self.assertEqual(len(coverage["category_matrix"]), 7)

    def test_invalid_media_and_coordinates_are_rejected(self):
        payload = self.payload()
        payload["latitude"] = 100
        with self.assertRaises(ValueError):
            self.registry.register(payload)
        item = self.registry.register(self.payload())
        bad = self.observation()
        bad["content_type"] = "text/html"
        with self.assertRaises(ValueError):
            self.registry.observe(item["source_id"], bad)

    def test_idempotent_registration_preserves_health(self):
        first = self.registry.register(self.payload())
        self.registry.observe(first["source_id"], self.observation())
        changed = self.payload()
        changed["title"] = "Updated title"
        second = self.registry.register(changed)
        self.assertEqual(first["source_id"], second["source_id"])
        self.assertEqual(second["state"], "FRESH")
        self.assertEqual(second["title"], "Updated title")


if __name__ == "__main__":
    unittest.main()
