from __future__ import annotations

import io
import json
import struct
import unittest
from datetime import datetime, timezone

from phase34_complete import Phase34Application
from phase34_imagery import ImageRegistry
from phase35_complete import Phase35Application
from phase35_ingestion import ImageryIngestionEngine, IngestionStore
from phase35_sources import HttpResponse, HttpTransport, ImageCandidate, NasaEpicAdapter, SourceAdapter, SourceError, image_dimensions


def png(width=32, height=16):
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 8 + struct.pack(">II", width, height) + b"\x00" * 16


class FakeTransport:
    def __init__(self, image=None):
        self.image = image or png()

    def get(self, url, *, allowed_hosts, max_bytes):
        self.last = (url, allowed_hosts, max_bytes)
        return HttpResponse(
            status=200,
            url=url,
            headers={"content-type": "image/png", "last-modified": "Mon, 27 Jul 2026 12:00:00 GMT"},
            body=self.image,
        )

    def get_json(self, url, *, allowed_hosts, max_bytes=2_000_000):
        return [
            {
                "identifier": "20260727120000",
                "caption": "Earth",
                "image": "epic_1b_20260727120000",
                "version": "03",
                "date": "2026-07-27 12:00:00",
                "centroid_coordinates": {"lat": 12.5, "lon": 81.2},
            }
        ]


class FixtureAdapter(SourceAdapter):
    name = "fixture"

    def discover(self, transport):
        return [
            ImageCandidate(
                adapter=self.name,
                external_id="fixture-1",
                source_payload={
                    "region": "Asia",
                    "country": "",
                    "title": "Fixture satellite image",
                    "category": "satellite",
                    "geographic_scope": "Test scope",
                    "source_url": "https://example.gov/source",
                    "image_url": "https://example.gov/image.png",
                    "latitude": 10,
                    "longitude": 70,
                    "provider": "Fixture government provider",
                    "attribution": "Credit fixture",
                    "license_note": "Public test fixture",
                    "refresh_interval_seconds": 300,
                    "max_age_seconds": 604800,
                },
                captured_at="2026-07-27T12:00:00Z",
                image_url="https://example.gov/image.png",
                allowed_hosts=("example.gov",),
                metadata={"fixture": True},
            )
        ]


class Phase35Tests(unittest.TestCase):
    def test_release_is_forward_compatible(self):
        self.assertTrue(issubclass(Phase35Application, Phase34Application))

    def test_nasa_adapter_discovers_real_archive_shape(self):
        candidate = NasaEpicAdapter().discover(FakeTransport())[0]
        self.assertEqual(candidate.adapter, "nasa-epic")
        self.assertEqual(candidate.source_payload["region"], "Asia")
        self.assertIn("/2026/07/27/png/epic_1b_20260727120000.png", candidate.image_url)
        self.assertEqual(candidate.captured_at, "2026-07-27T12:00:00Z")

    def test_engine_fetches_validates_hashes_qualifies_and_persists(self):
        registry = ImageRegistry()
        store = IngestionStore(":memory:")
        engine = ImageryIngestionEngine(registry, store, transport=FakeTransport())
        result = engine.run_adapter(FixtureAdapter())
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["succeeded"], 1)
        item = registry.list()[0]
        self.assertEqual(item["state"], "FRESH")
        self.assertEqual(item["width"], 32)
        self.assertEqual(item["height"], 16)
        self.assertEqual(len(item["content_sha256"]), 64)
        self.assertEqual(store.runs()[0]["status"], "SUCCESS")
        self.assertEqual(store.observations()[0]["source_id"], item["source_id"])

    def test_invalid_image_is_recorded_as_failure(self):
        registry = ImageRegistry()
        store = IngestionStore(":memory:")
        engine = ImageryIngestionEngine(registry, store, transport=FakeTransport(b"not-an-image"))
        result = engine.run_adapter(FixtureAdapter())
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["failed"], 1)
        self.assertTrue(store.observations()[0]["error"])

    def test_transport_blocks_non_https_and_unlisted_hosts(self):
        transport = HttpTransport(retries=1)
        with self.assertRaises(SourceError):
            transport.get("http://example.gov/a", allowed_hosts={"example.gov"}, max_bytes=100)
        with self.assertRaises(SourceError):
            transport.get("https://evil.example/a", allowed_hosts={"example.gov"}, max_bytes=100)

    def test_png_dimension_parser(self):
        self.assertEqual(image_dimensions(png(1250, 750), "image/png"), (1250, 750))


if __name__ == "__main__":
    unittest.main()
