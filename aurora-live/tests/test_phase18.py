import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phase14_integrity import EvidenceIntegrity
from phase15_mesh import SensorMesh
from phase16_synchronized import DetectionEngine
from phase17_fabric import RealtimeFabric
from phase18_graph import EntityGraph
from storage import Store


class Phase18Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "phase18.db")
        _, token = self.store.create_user(f"admin-{uuid.uuid4().hex}@example.com", "admin")
        self.actor = self.store.auth(token)
        self.mesh = SensorMesh(self.store)
        self.integrity = EvidenceIntegrity(self.store)
        self.detection = DetectionEngine(self.store, self.mesh, self.integrity)
        self.fabric = RealtimeFabric(self.store, self.detection)
        self.graph = EntityGraph(self.store, self.detection, self.fabric)

    def tearDown(self):
        self.temp.cleanup()

    def test_entity_alias_and_relation_resolution(self):
        actor = self.graph.upsert_entity(self.actor, {"name": "Example Group", "entity_type": "ORGANIZATION", "aliases": ["EG"]})
        asset = self.graph.upsert_entity(self.actor, {"name": "Vessel One", "entity_type": "VESSEL"})
        self.assertEqual(self.graph.resolve(self.actor, "EG", "ORGANIZATION")["id"], actor["id"])
        relation = self.graph.relate(self.actor, {"subject_id": actor["id"], "relation_type": "OPERATES", "object_id": asset["id"], "confidence": 0.9})
        self.assertEqual(relation["relation_type"], "OPERATES")
        view = self.graph.graph(self.actor, actor["id"])
        self.assertEqual((view["node_count"], view["edge_count"]), (2, 1))

    def test_alias_conflict_is_rejected(self):
        first = self.graph.upsert_entity(self.actor, {"name": "Alpha", "entity_type": "ORGANIZATION", "aliases": ["Common"]})
        second = self.graph.upsert_entity(self.actor, {"name": "Beta", "entity_type": "ORGANIZATION"})
        self.assertNotEqual(first["id"], second["id"])
        with self.assertRaises(ValueError):
            self.graph.add_alias(self.actor, second["id"], {"alias": "Common", "confidence": 0.9})

    def test_merge_and_split_are_revisioned(self):
        source = self.graph.upsert_entity(self.actor, {"name": "Acme Incorporated", "entity_type": "ORGANIZATION"})
        target = self.graph.upsert_entity(self.actor, {"name": "Acme Inc", "entity_type": "ORGANIZATION"})
        self.assertEqual(self.graph.merge(self.actor, source["id"], target["id"], "same legal entity")["source"]["status"], "MERGED")
        self.assertEqual(self.graph.split(self.actor, source["id"], {"reason": "false merge"})["status"], "ACTIVE")
        actions = {item["action"] for item in self.graph.revisions(self.actor, "entity", source["id"])}
        self.assertTrue({"MERGE", "SPLIT"}.issubset(actions))

    def test_fabric_materializes_detection_event(self):
        observation = {"external_id": "event-1", "title": "Service disruption reported near Port Delta", "observed_at": "2026-07-23T00:00:00Z", "latitude": 10.0, "longitude": 20.0, "location_name": "Port Delta", "entities": [{"name": "Example Group", "type": "ORGANIZATION", "confidence": 0.8}]}
        self.mesh.ingest_observations(self.actor, "news.gdelt", [observation])
        self.assertEqual(self.fabric.process_pending(self.actor)["events_published"], 1)
        self.assertEqual(self.graph.process_fabric(self.actor)["materialized"], 1)
        self.assertGreaterEqual(self.graph.scorecard(self.actor)["entities_by_type"].get("EVENT", 0), 1)
        self.assertEqual(self.graph.process_fabric(self.actor)["processed"], 0)

    def test_workspace_isolation(self):
        entity = self.graph.upsert_entity(self.actor, {"name": "Private Entity", "entity_type": "OTHER"})
        other = dict(self.actor)
        other["workspace_id"] = "other-workspace"
        with self.assertRaises(KeyError):
            self.graph.entity(other, entity["id"])


if __name__ == "__main__":
    unittest.main()
