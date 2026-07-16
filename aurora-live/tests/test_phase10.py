import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phase10_geo import GeoIndex, GeoQuery, parse_bbox


INCIDENTS = [
    {"id": "a", "latitude": 40.7, "longitude": -74.0, "category": "infrastructure", "severity": "high", "published_at": "2026-07-16T00:00:00Z", "location_name": "United States"},
    {"id": "b", "latitude": 40.8, "longitude": -73.9, "category": "infrastructure", "severity": "medium", "published_at": "2026-07-16T01:00:00Z", "location_name": "United States"},
    {"id": "c", "latitude": 35.7, "longitude": 139.7, "category": "disaster", "severity": "critical", "published_at": "2026-07-16T02:00:00Z", "location_name": "Japan"},
    {"id": "missing", "category": "world", "severity": "low"},
]


class Phase10Tests(unittest.TestCase):
    def test_bbox_filter_and_dateline_validation(self):
        index = GeoIndex(INCIDENTS)
        query = GeoQuery(bbox=parse_bbox("-80,35,-70,45"))
        self.assertEqual({item["id"] for item in index.filter(query)}, {"a", "b"})
        with self.assertRaises(ValueError):
            parse_bbox("-200,0,10,20")

    def test_cluster_groups_nearby_incidents(self):
        index = GeoIndex(INCIDENTS)
        clusters = index.clusters(GeoQuery(zoom=3))
        self.assertEqual(sum(cluster["count"] for cluster in clusters), 3)
        self.assertGreaterEqual(clusters[0]["count"], 1)

    def test_heat_weights_critical_incidents_higher(self):
        index = GeoIndex(INCIDENTS)
        cells = index.heat(GeoQuery())
        self.assertGreaterEqual(cells[0]["weight"], 8)

    def test_country_dossier_summarizes_risk(self):
        index = GeoIndex(INCIDENTS)
        dossier = index.dossier("United States", INCIDENTS)
        self.assertEqual(dossier["incident_count"], 2)
        self.assertGreater(dossier["risk_score"], 0)

    def test_category_and_time_filtering(self):
        index = GeoIndex(INCIDENTS)
        query = GeoQuery(categories=frozenset({"disaster"}), since="2026-07-16T01:30:00Z")
        self.assertEqual([item["id"] for item in index.filter(query)], ["c"])


if __name__ == "__main__":
    unittest.main()
