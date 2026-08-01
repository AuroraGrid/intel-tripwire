from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from phase36_complete import Phase36Application
from phase37_capabilities import reconciled_manifest
from phase37_complete import Phase37Application
from phase37_webcams import (
    DurableWebcamRegistry,
    ProbeResponse,
    WebcamHealthCoordinator,
    WebcamStore,
)


REGIONS = (
    "Oceania",
    "Africa",
    "Asia",
    "Middle East",
    "Europe",
    "North America",
    "South America",
)


def source(region: str, index: int, source_type: str = "hls") -> dict:
    slug = region.lower().replace(" ", "-")
    return {
        "region": region,
        "country": f"Country {index}",
        "city": f"City {index}",
        "title": f"{region} camera {index}",
        "source_type": source_type,
        "source_url": f"https://example.com/{slug}/{index}.m3u8",
        "embed_url": f"https://example.com/{slug}/{index}.m3u8",
        "latitude": max(-89.0, min(89.0, -40.0 + index)),
        "longitude": max(-179.0, min(179.0, -120.0 + index)),
        "provider": "Qualification Fixture",
        "attribution": "Qualification Fixture",
        "license_note": "Test-only source metadata",
    }


def imagery(qualified: int) -> dict:
    return {
        "qualified_regions": qualified,
        "fully_qualified": qualified == 7,
        "regions": [{"region": region, "verified": index < qualified} for index, region in enumerate(REGIONS)],
    }


def health() -> dict:
    return {"state": "ONLINE", "feeds": [{"feed": "events", "state": "ONLINE"}]}


class Phase37Tests(unittest.TestCase):
    def test_release_is_forward_compatible(self):
        self.assertTrue(issubclass(Phase37Application, Phase36Application))

    def test_registration_alone_is_partial_not_live(self):
        registry = DurableWebcamRegistry(WebcamStore(":memory:"))
        registry.register(source("Europe", 1))
        manifest = reconciled_manifest(
            webcam_coverage=registry.coverage(),
            imagery_baseline=imagery(0),
            unified_health=health(),
        )
        webcam = next(item for item in manifest["capabilities"] if item["key"] == "webcams")
        self.assertEqual(webcam["declared_status"], "PLANNED")
        self.assertEqual(webcam["status"], "PARTIAL")
        self.assertIn("registration is not live evidence", webcam["runtime_evidence"])

    def test_seventy_independently_observed_online_cameras_qualify(self):
        registry = DurableWebcamRegistry(WebcamStore(":memory:"))
        for region in REGIONS:
            for index in range(10):
                item = registry.register(source(region, index))
                registry.observe(item["webcam_id"], {"health": "ONLINE", "detail": {"proof": "test"}})
        coverage = registry.coverage()
        matrix = registry.matrix()
        self.assertTrue(coverage["fully_qualified"])
        self.assertEqual(coverage["total_online"], 70)
        self.assertEqual(matrix["qualified_slots"], 70)
        self.assertTrue(matrix["fully_qualified"])
        manifest = reconciled_manifest(
            webcam_coverage=coverage,
            imagery_baseline=imagery(7),
            unified_health=health(),
        )
        webcam = next(item for item in manifest["capabilities"] if item["key"] == "webcams")
        satellite = next(item for item in manifest["capabilities"] if item["key"] == "satellite-imagery")
        self.assertEqual(webcam["status"], "LIVE")
        self.assertEqual(satellite["status"], "LIVE")

    def test_webcam_and_health_history_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "webcams.sqlite3")
            first = DurableWebcamRegistry(WebcamStore(path))
            item = first.register(source("Asia", 1))
            first.observe(item["webcam_id"], {"health": "ONLINE", "detail": {"status": 200}})
            second_store = WebcamStore(path)
            second = DurableWebcamRegistry(second_store)
            restored = second.get(item["webcam_id"])
            history = second_store.health_history(item["webcam_id"])
            self.assertEqual(restored["health"], "ONLINE")
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["detail"]["status"], 200)
            # Close database connections to avoid Windows file locks during tempdir cleanup
            first.store.close()
            second_store.close()
            import gc
            gc.collect()

    def test_hls_requires_playlist_evidence(self):
        item = source("North America", 1)
        valid = ProbeResponse(
            status=200,
            url=item["source_url"],
            headers={"content-type": "application/vnd.apple.mpegurl"},
            body=b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1280000\nstream.m3u8\n",
        )
        health_state, detail = WebcamHealthCoordinator.classify(item, valid)
        self.assertEqual(health_state, "ONLINE")
        self.assertEqual(detail["verification"], "valid-hls-playlist")
        unproven = ProbeResponse(
            status=200,
            url=item["source_url"],
            headers={"content-type": "text/html"},
            body=b"<html>camera page</html>",
        )
        health_state, detail = WebcamHealthCoordinator.classify(item, unproven)
        self.assertEqual(health_state, "DEGRADED")
        self.assertEqual(detail["verification"], "reachable-without-valid-hls-playlist")

    def test_repeated_degraded_observations_force_offline(self):
        registry = DurableWebcamRegistry(WebcamStore(":memory:"))
        item = registry.register(source("Africa", 1))
        for _ in range(3):
            result = registry.observe(item["webcam_id"], {"health": "DEGRADED"})
        self.assertEqual(result["health"], "OFFLINE")
        self.assertEqual(result["consecutive_failures"], 3)

    @unittest.skipUnless(os.getenv("AURORA_PHASE37_POSTGRES_DSN"), "PostgreSQL DSN not configured")
    def test_postgres_webcam_store_contract(self):
        registry = DurableWebcamRegistry(WebcamStore(os.environ["AURORA_PHASE37_POSTGRES_DSN"]))
        item = registry.register(source("Oceania", 937))
        registry.observe(item["webcam_id"], {"health": "ONLINE", "detail": {"database": "postgres"}})
        restored = registry.get(item["webcam_id"])
        self.assertEqual(restored["health"], "ONLINE")
        self.assertTrue(registry.store.health_history(item["webcam_id"]))


if __name__ == "__main__":
    unittest.main()
