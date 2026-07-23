import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phase15_mesh import SensorMesh
from phase20_operating_picture import LiveOperatingPicture, now
from storage import Store


class Phase20Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "phase20.db")
        _, token = self.store.create_user(
            f"admin-{uuid.uuid4().hex}@example.com", "admin"
        )
        self.actor = self.store.auth(token)
        self.mesh = SensorMesh(self.store)
        self.picture = LiveOperatingPicture(self.store, self.mesh)

    def tearDown(self):
        self.temp.cleanup()

    def asset(self, **overrides):
        payload = {
            "asset_type": "AIRCRAFT",
            "provider": "test-provider",
            "external_id": f"asset-{uuid.uuid4().hex}",
            "display_name": "Test Aircraft",
            "sensitivity": "PUBLIC",
        }
        payload.update(overrides)
        return self.picture.upsert_asset(self.actor, payload)

    def position(self, asset_id, **overrides):
        payload = {
            "latitude": 40.0,
            "longitude": -73.0,
            "observed_at": now(),
            "speed": 240,
            "heading": 90,
            "source_observation_id": f"position-{uuid.uuid4().hex}",
        }
        payload.update(overrides)
        return self.picture.ingest_position(self.actor, asset_id, payload)

    def test_public_position_is_visible_in_geojson(self):
        asset = self.asset()
        position = self.position(asset["id"])
        self.assertTrue(position["publicly_visible"])
        features = self.picture.geojson(
            self.actor, {"include_infrastructure": "false"}
        )["features"]
        self.assertEqual(len(features), 1)
        self.assertEqual(features[0]["properties"]["asset_id"], asset["id"])

    def test_military_asset_is_automatically_delayed(self):
        asset = self.asset(military=True)
        self.assertEqual(asset["sensitivity"], "DELAYED")
        position = self.position(asset["id"])
        self.assertFalse(position["publicly_visible"])
        self.assertEqual(self.picture.positions(self.actor), [])
        self.assertEqual(self.picture.track(self.actor, asset["id"])["positions"], [])

    def test_restricted_track_is_never_exposed(self):
        asset = self.asset(sensitivity="RESTRICTED")
        self.position(asset["id"])
        self.assertEqual(self.picture.positions(self.actor), [])
        with self.assertRaises(KeyError):
            self.picture.track(self.actor, asset["id"])

    def test_structured_anomalies_are_visible_and_reviewable(self):
        asset = self.asset(asset_type="VESSEL", display_name="Test Vessel")
        self.position(
            asset["id"], speed=0.2, loitering_minutes=90,
            ais_gap_hours=7, gps_interference=True,
        )
        anomalies = self.picture.anomalies(self.actor)
        kinds = {item["anomaly_type"] for item in anomalies}
        self.assertEqual(kinds, {"GPS_INTERFERENCE", "DARK_GAP", "LOITERING"})
        reviewed = self.picture.review_anomaly(
            self.actor, anomalies[0]["id"],
            {"state": "ACKNOWLEDGED", "rationale": "Analyst triage"},
        )
        self.assertEqual(reviewed["state"], "ACKNOWLEDGED")

    def test_infrastructure_is_delivered_as_geojson(self):
        item = self.picture.upsert_infrastructure(self.actor, {
            "infrastructure_type": "PORT",
            "provider": "official-catalog",
            "external_id": "port-delta",
            "name": "Port Delta",
            "country_code": "US",
            "latitude": 29.5,
            "longitude": -90.2,
            "source_url": "https://example.gov/port-delta",
        })
        self.assertEqual(item["geometry"]["type"], "Point")
        features = self.picture.geojson(self.actor)["features"]
        self.assertEqual(features[0]["properties"]["kind"], "INFRASTRUCTURE")

    def test_mesh_observation_materialization_is_idempotent(self):
        result = self.mesh.ingest_observations(self.actor, "aviation.opensky", [{
            "external_id": "observation-aircraft-1",
            "title": "Aircraft position",
            "observed_at": now(),
            "latitude": 51.5,
            "longitude": -0.1,
            "icao24": "abc123",
            "callsign": "TEST123",
            "speed": 180,
            "heading": 270,
            "provider": "OpenSky",
        }])
        self.assertEqual(result["accepted"], 1)
        first = self.picture.process_mesh(self.actor)
        second = self.picture.process_mesh(self.actor)
        self.assertEqual(first["positions_materialized"], 1)
        self.assertEqual(second["positions_materialized"], 1)
        self.assertEqual(len(self.picture.positions(self.actor)), 1)

    def test_track_distance_and_bbox_filter(self):
        asset = self.asset()
        self.position(
            asset["id"], latitude=40.0, longitude=-73.0,
            source_observation_id="track-a",
        )
        self.position(
            asset["id"], latitude=41.0, longitude=-73.0,
            source_observation_id="track-b",
        )
        track = self.picture.track(self.actor, asset["id"])
        self.assertGreater(track["distance_km"], 100)
        filtered = self.picture.positions(
            self.actor, {"min_lat": 40.5, "max_lat": 41.5}
        )
        self.assertEqual(len(filtered), 1)

    def test_workspace_isolation(self):
        asset = self.asset()
        self.position(asset["id"])
        other = dict(self.actor)
        other["workspace_id"] = "other-workspace"
        with self.assertRaises(KeyError):
            self.picture.asset(other, asset["id"])
        self.assertEqual(self.picture.positions(other), [])

    def test_scorecard_exposes_tracking_policy(self):
        self.asset(military=True)
        card = self.picture.scorecard(self.actor)
        self.assertEqual(card["phase"], 20)
        self.assertTrue(card["public_tracking_policy"]["restricted_excluded"])
        self.assertGreaterEqual(
            card["public_tracking_policy"]["default_sensitive_delay_seconds"], 3600
        )


if __name__ == "__main__":
    unittest.main()
