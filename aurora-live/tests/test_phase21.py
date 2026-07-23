import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phase15_mesh import SensorMesh
from phase20_operating_picture import LiveOperatingPicture
from phase21_routes import RouteIntelligence
from storage import Store


class Phase21Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "phase21.db")
        _, token = self.store.create_user(
            f"admin-{uuid.uuid4().hex}@example.com", "admin"
        )
        self.actor = self.store.auth(token)
        self.picture = LiveOperatingPicture(self.store, SensorMesh(self.store))
        self.routes = RouteIntelligence(self.store, self.picture)
        self.a = self.node("Origin Port", "PORT", 0, 0, "AA")
        self.b = self.node("Primary Strait", "CHOKEPOINT", 0, 2, "BB")
        self.c = self.node("Alternate Cape", "CHOKEPOINT", 2, 0, "CC")
        self.d = self.node("Destination Port", "PORT", 2, 2, "DD")
        self.ab = self.edge("ab", self.a, self.b, 10, 100, 0.05)
        self.bd = self.edge("bd", self.b, self.d, 10, 100, 0.05)
        self.ac = self.edge("ac", self.a, self.c, 16, 130, 0.10)
        self.cd = self.edge("cd", self.c, self.d, 16, 130, 0.10)

    def tearDown(self):
        self.temp.cleanup()

    def node(self, name, node_type, latitude, longitude, country):
        return self.routes.upsert_node(self.actor, {
            "node_type": node_type,
            "name": name,
            "country_code": country,
            "latitude": latitude,
            "longitude": longitude,
        })

    def edge(self, external_id, origin, destination, hours, cost, risk, **extra):
        payload = {
            "provider": "test-network",
            "external_id": external_id,
            "from_node_id": origin["id"],
            "to_node_id": destination["id"],
            "mode": "SEA",
            "transit_hours": hours,
            "base_cost": cost,
            "base_risk": risk,
            "bidirectional": False,
        }
        payload.update(extra)
        return self.routes.upsert_edge(self.actor, payload)

    def plan(self, name="Test shipment"):
        return self.routes.create_plan(self.actor, {
            "name": name,
            "origin_node_id": self.a["id"],
            "destination_node_id": self.d["id"],
            "commodity": "grain",
            "volume": 1000,
            "allowed_modes": ["SEA"],
        })

    def test_selects_deterministic_lowest_weight_route_and_alternative(self):
        plan = self.plan()
        selected = [item["edge_id"] for item in plan["selected_path"]["segments"]]
        alternative = [item["edge_id"] for item in plan["alternatives"][0]["segments"]]
        self.assertEqual(selected, [self.ab["id"], self.bd["id"]])
        self.assertEqual(alternative, [self.ac["id"], self.cd["id"]])
        self.assertEqual(plan["action_state"], "MONITOR")
        self.assertEqual(plan["transit_hours"], 20)

    def test_disruption_reroutes_and_writes_revision(self):
        plan = self.plan()
        self.routes.record_disruption(self.actor, {
            "title": "Primary strait closure risk",
            "edge_id": self.bd["id"],
            "probability": 1.0,
            "impact": 1.0,
            "source_type": "detection",
            "source_id": "detection-closure-1",
            "evidence": {"claim_state": "SUPPORTED"},
        })
        updated = self.routes.recalculate(
            self.actor, plan["id"], "New supported closure detection"
        )
        selected = [item["edge_id"] for item in updated["selected_path"]["segments"]]
        self.assertEqual(selected, [self.ac["id"], self.cd["id"]])
        self.assertEqual(len(self.routes.revisions(self.actor, plan["id"])), 2)

    def test_chokepoint_country_and_company_exposures(self):
        self.routes.upsert_edge(self.actor, {
            "provider": "test-network",
            "external_id": "bd",
            "from_node_id": self.b["id"],
            "to_node_id": self.d["id"],
            "mode": "SEA",
            "transit_hours": 10,
            "base_cost": 100,
            "base_risk": 0.05,
            "bidirectional": False,
            "attributes": {"companies": ["Example Shipping"]},
        })
        plan = self.plan("Exposure shipment")
        types = {item["exposure_type"] for item in plan["exposures"]}
        self.assertTrue({"COUNTRY", "CHOKEPOINT", "COMPANY"}.issubset(types))

    def test_closed_edges_block_route_when_no_alternative_remains(self):
        for edge_id, origin, destination, hours, cost, risk in (
            ("ab", self.a, self.b, 10, 100, 0.05),
            ("ac", self.a, self.c, 16, 130, 0.10),
        ):
            self.edge(
                edge_id, origin, destination, hours, cost, risk, status="CLOSED"
            )
        plan = self.plan("Blocked shipment")
        self.assertEqual(plan["action_state"], "BLOCKED")
        self.assertEqual(plan["risk_score"], 100)
        self.assertIn("No open route", plan["binding_constraint"])

    def test_geojson_contains_selected_route_segments(self):
        plan = self.plan()
        view = self.routes.geojson(self.actor, plan["id"])
        self.assertEqual(view["type"], "FeatureCollection")
        self.assertEqual(len(view["features"]), 2)
        self.assertEqual(view["features"][0]["geometry"]["type"], "LineString")

    def test_operating_infrastructure_import(self):
        self.picture.upsert_infrastructure(self.actor, {
            "infrastructure_type": "PORT",
            "provider": "official",
            "external_id": "port-z",
            "name": "Port Z",
            "country_code": "ZZ",
            "latitude": 12,
            "longitude": 13,
        })
        result = self.routes.import_infrastructure(self.actor)
        self.assertEqual(result["imported"], 1)
        self.assertGreaterEqual(self.routes.scorecard(self.actor)["nodes"]["PORT"], 3)

    def test_recalculation_is_stable_without_material_change(self):
        plan = self.plan()
        revision_count = len(self.routes.revisions(self.actor, plan["id"]))
        first = self.routes.recalculate(self.actor, plan["id"])
        second = self.routes.recalculate(self.actor, plan["id"])
        self.assertEqual(first["selected_path"], second["selected_path"])
        self.assertEqual(
            len(self.routes.revisions(self.actor, plan["id"])), revision_count
        )

    def test_workspace_isolation(self):
        plan = self.plan()
        other = dict(self.actor)
        other["workspace_id"] = "other-workspace"
        with self.assertRaises(KeyError):
            self.routes.plan(other, plan["id"])
        self.assertEqual(self.routes.plans(other), [])


if __name__ == "__main__":
    unittest.main()
