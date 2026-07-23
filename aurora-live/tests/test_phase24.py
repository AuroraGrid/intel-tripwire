import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phase11_store import ForecastLedger
from phase14_integrity import EvidenceIntegrity
from phase15_mesh import SensorMesh
from phase16_synchronized import DetectionEngine
from phase17_fabric import RealtimeFabric
from phase20_operating_picture import LiveOperatingPicture
from phase21_routes import RouteIntelligence
from phase22_forecasting import AutonomousForecastEngine
from phase23_experience import UnifiedAnalystExperience
from phase24_ecosystem import DeveloperEcosystem
from phase24_mcp import AuroraMCPServer
from storage import Store


class Phase24Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "phase24.db")
        _, token = self.store.create_user(
            f"admin-{uuid.uuid4().hex}@example.com", "admin"
        )
        self.actor = self.store.auth(token)
        self.mesh = SensorMesh(self.store)
        self.integrity = EvidenceIntegrity(self.store)
        self.detection = DetectionEngine(
            self.store, self.mesh, self.integrity
        )
        self.fabric = RealtimeFabric(self.store, self.detection)
        self.picture = LiveOperatingPicture(self.store, self.mesh)
        self.routes = RouteIntelligence(self.store, self.picture)
        self.forecasts = ForecastLedger(self.store)
        self.autonomous = AutonomousForecastEngine(
            self.store, self.forecasts, self.detection, self.routes
        )
        self.command = UnifiedAnalystExperience(
            self.store,
            self.mesh,
            self.integrity,
            self.detection,
            self.picture,
            self.routes,
            self.autonomous,
        )
        self.developer = DeveloperEcosystem(self.store)
        self.mcp = AuroraMCPServer(
            self.command,
            self.detection,
            self.routes,
            self.autonomous,
            self.integrity,
            self.mesh,
            self.fabric,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_service_secret_is_shown_once_scoped_and_revocable(self):
        created = self.developer.create_client(self.actor, {
            "name": "Public dashboard",
            "scopes": ["read"],
        })
        self.assertTrue(created["secret"].startswith("aurora_sk_"))
        listed = self.developer.clients(self.actor)[0]
        self.assertNotIn("secret", listed)
        authenticated = self.developer.authenticate(created["secret"])
        self.assertEqual(authenticated["workspace_id"], self.actor["workspace_id"])
        self.assertEqual(authenticated["api_scopes"], ["read"])
        self.developer.require_scope(authenticated, "read")
        with self.assertRaises(PermissionError):
            self.developer.require_scope(authenticated, "forecasts:write")
        self.developer.revoke(self.actor, created["id"])
        with self.assertRaises(PermissionError):
            self.developer.authenticate(created["secret"])

    def test_cursor_is_opaque_stable_and_rejects_malformed_input(self):
        cursor = self.developer.encode_cursor(175)
        self.assertNotIn("175", cursor)
        self.assertEqual(self.developer.decode_cursor(cursor), 175)
        with self.assertRaises(ValueError):
            self.developer.decode_cursor("not-a-valid-cursor")

    def test_idempotency_replays_same_request_and_rejects_conflict(self):
        calls = []
        first = self.developer.idempotent(
            self.actor,
            "approval-123",
            "approve",
            {"candidate": "one"},
            lambda: (calls.append("called") or (200, {"ok": True})),
        )
        second = self.developer.idempotent(
            self.actor,
            "approval-123",
            "approve",
            {"candidate": "one"},
            lambda: (500, {"should": "not execute"}),
        )
        self.assertFalse(first[2])
        self.assertTrue(second[2])
        self.assertEqual(calls, ["called"])
        with self.assertRaises(ValueError):
            self.developer.idempotent(
                self.actor,
                "approval-123",
                "approve",
                {"candidate": "different"},
                lambda: (200, {}),
            )

    def test_openapi_and_agent_manifest_are_machine_discoverable(self):
        spec = self.developer.openapi()
        manifest = self.developer.agent_manifest()
        self.assertEqual(spec["openapi"], "3.1.0")
        self.assertEqual(spec["info"]["version"], "24.0.0")
        self.assertIn("/api/v1/detections", spec["paths"])
        self.assertEqual(manifest["discovery"]["mcp"], "/mcp")
        self.assertFalse(manifest["safety"]["external_ai_required"])

    def test_mcp_initialization_listing_and_tool_call(self):
        initialized = self.mcp.handle(self.actor, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {},
        })
        self.assertEqual(
            initialized["result"]["protocolVersion"], "2025-06-18"
        )
        tools = self.mcp.handle(self.actor, {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        })
        self.assertGreaterEqual(len(tools["result"]["tools"]), 10)
        call = self.mcp.handle(self.actor, {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "get_command_center",
                "arguments": {},
            },
        })
        self.assertFalse(call["result"]["isError"])
        self.assertEqual(
            call["result"]["structuredContent"]["phase"], 23
        )

    def test_mcp_tool_annotations_are_read_only_and_workspace_safe(self):
        manifest = self.mcp.manifest()
        self.assertTrue(manifest["safety"]["read_only_tools"])
        for tool in manifest["tools"]:
            self.assertTrue(tool["annotations"]["readOnlyHint"])
            self.assertFalse(tool["annotations"]["destructiveHint"])

    def test_scorecard_reports_sdk_and_protocol_capabilities(self):
        card = self.developer.scorecard(self.actor)
        self.assertEqual(card["phase"], 24)
        self.assertTrue(card["capabilities"]["mcp_json_rpc"])
        self.assertTrue(card["capabilities"]["python_sdk"])
        self.assertTrue(card["capabilities"]["typescript_sdk"])
        self.assertTrue(card["capabilities"]["idempotency_keys"])


if __name__ == "__main__":
    unittest.main()
