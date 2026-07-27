import io
import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from identity import CURRENT_WORKSPACE
from phase30_complete import Phase30Application
from phase30_distribution import DistributionHub
from platform_wsgi import create_application
from production_wsgi import ProductionApplication
from storage import Store


def request(app, path, method="GET", body=None, token=""):
    raw = b"" if body is None else json.dumps(body).encode()
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path.split("?", 1)[0],
        "QUERY_STRING": path.split("?", 1)[1] if "?" in path else "",
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "80",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "wsgi.version": (1, 0),
        "wsgi.url_scheme": "http",
        "wsgi.input": io.BytesIO(raw),
        "wsgi.errors": io.StringIO(),
        "wsgi.multithread": False,
        "wsgi.multiprocess": False,
        "wsgi.run_once": False,
        "CONTENT_LENGTH": str(len(raw)),
        "CONTENT_TYPE": "application/json",
        "REMOTE_ADDR": "127.0.0.1",
        "HTTP_HOST": "localhost",
    }
    if token:
        environ["HTTP_AUTHORIZATION"] = "Bearer " + token
    captured = {}

    def start(status, headers):
        captured.update(status=status, headers=dict(headers))

    result = b"".join(app(environ, start))
    return {
        "code": int(captured["status"].split()[0]),
        "json": json.loads(result) if result else None,
    }


class Phase30Tests(unittest.TestCase):
    def setUp(self):
        CURRENT_WORKSPACE.set(None)
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "phase30.db")
        _, self.token = self.store.create_user(
            f"admin-{uuid.uuid4().hex}@example.com", "admin"
        )
        self.actor = self.store.auth(self.token)
        self.hub = DistributionHub(self.store)

    def tearDown(self):
        CURRENT_WORKSPACE.set(None)
        self.temp.cleanup()

    def channel_payload(self, **updates):
        payload = {
            "channel_key": "internal-briefs",
            "name": "Internal briefs",
            "channel_type": "QUEUE",
            "clearance": "INTERNAL",
            "destination": "queue://internal-briefs",
            "active": True,
        }
        payload.update(updates)
        return payload

    def package_payload(self, **updates):
        payload = {
            "package_key": "daily-brief",
            "classification": "INTERNAL",
            "title": "Daily intelligence brief",
            "payload": {"claims": [{"id": "claim-1", "status": "SUPPORTED"}]},
        }
        payload.update(updates)
        return payload

    def test_package_is_deterministic_and_idempotent(self):
        first = self.hub.create_package(self.actor, self.package_payload())
        second = self.hub.create_package(self.actor, self.package_payload())
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["sha256"], second["manifest"]["sha256"])
        self.assertEqual(len(first["sha256"]), 64)

    def test_clearance_blocks_delivery(self):
        channel = self.hub.upsert_channel(
            self.actor, self.channel_payload(clearance="PUBLIC")
        )
        package = self.hub.create_package(
            self.actor, self.package_payload(classification="CONFIDENTIAL")
        )
        with self.assertRaises(PermissionError):
            self.hub.queue_delivery(
                self.actor,
                {
                    "package_id": package["id"],
                    "channel_id": channel["id"],
                    "idempotency_key": "delivery-1",
                },
            )

    def test_delivery_queue_and_receipt(self):
        channel = self.hub.upsert_channel(self.actor, self.channel_payload())
        package = self.hub.create_package(self.actor, self.package_payload())
        first = self.hub.queue_delivery(
            self.actor,
            {
                "package_id": package["id"],
                "channel_id": channel["id"],
                "idempotency_key": "delivery-2",
            },
        )
        second = self.hub.queue_delivery(
            self.actor,
            {
                "package_id": package["id"],
                "channel_id": channel["id"],
                "idempotency_key": "delivery-2",
            },
        )
        self.assertEqual(first["id"], second["id"])
        delivered = self.hub.record_delivery(
            self.actor,
            {
                "delivery_id": first["id"],
                "status": "DELIVERED",
                "evidence": {"receipt": "receipt-2.json"},
            },
        )
        self.assertEqual(delivered["status"], "DELIVERED")

    def test_workspace_isolation(self):
        channel = self.hub.upsert_channel(self.actor, self.channel_payload())
        other = dict(self.actor)
        other["workspace_id"] = "other-workspace"
        self.assertEqual(self.hub.channels(other), [])
        with self.assertRaises(KeyError):
            self.hub.channel(other, channel["id"])

    def test_application_routes_and_public_document(self):
        CURRENT_WORKSPACE.set(None)
        app = Phase30Application(
            base=ProductionApplication(create_application(store=self.store))
        )
        public = request(app, "/.well-known/aurora-distribution.json")
        self.assertEqual(public["json"]["phase"], 30)
        created = request(
            app,
            "/api/platform/distribution/channels",
            "POST",
            self.channel_payload(),
            self.token,
        )
        self.assertEqual(created["code"], 201)
        CURRENT_WORKSPACE.set(None)
        listed = request(
            app,
            "/api/platform/distribution/channels",
            token=self.token,
        )
        self.assertEqual(len(listed["json"]["channels"]), 1)


if __name__ == "__main__":
    unittest.main()
