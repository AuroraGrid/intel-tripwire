import io
import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from identity import CURRENT_WORKSPACE
from phase29_complete import Phase29Application
from phase29_enterprise import EnterpriseControlPlane
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


class Phase29Tests(unittest.TestCase):
    def setUp(self):
        CURRENT_WORKSPACE.set(None)
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "phase29.db")
        _, self.token = self.store.create_user(
            f"admin-{uuid.uuid4().hex}@example.com", "admin"
        )
        self.actor = self.store.auth(self.token)
        _, viewer_token = self.store.create_user(
            f"viewer-{uuid.uuid4().hex}@example.com", "viewer"
        )
        self.viewer = self.store.auth(viewer_token)
        self.viewer["workspace_id"] = self.actor["workspace_id"]
        self.enterprise = EnterpriseControlPlane(self.store)

    def tearDown(self):
        CURRENT_WORKSPACE.set(None)
        self.temp.cleanup()

    def policy_payload(self, **updates):
        payload = {
            "policy_key": "production-baseline",
            "title": "Production baseline",
            "controls": {
                "encryption_at_rest": True,
                "encryption_in_transit": True,
                "audit_logging": True,
                "external_ai_enabled": False,
                "independent_security_review": "PASS",
            },
            "allowed_regions": ["US-EAST", "US-WEST"],
        }
        payload.update(updates)
        return payload

    def deployment_payload(self, **updates):
        payload = {
            "name": "primary-production",
            "environment": "PRODUCTION",
            "region": "US-EAST",
            "data_residency": "US-EAST",
            "encryption_at_rest": True,
            "encryption_in_transit": True,
            "audit_logging": True,
            "external_ai_enabled": False,
            "owner": "platform-team",
        }
        payload.update(updates)
        return payload

    def test_policy_versions_are_append_only(self):
        first = self.enterprise.publish_policy(self.actor, self.policy_payload())
        second = self.enterprise.publish_policy(
            self.actor, self.policy_payload(title="Updated baseline")
        )
        self.assertEqual((first["version"], second["version"]), (1, 2))
        self.assertEqual(
            len(self.enterprise.policies(self.actor, "production-baseline")), 2
        )

    def test_compliance_requires_current_attestation(self):
        self.enterprise.publish_policy(self.actor, self.policy_payload())
        deployment = self.enterprise.register_deployment(
            self.actor, self.deployment_payload()
        )
        self.assertEqual(
            self.enterprise.compliance(self.actor, deployment["id"])[
                "deployments"
            ][0]["status"],
            "NOT_VERIFIED",
        )
        payload = {
            "deployment_id": deployment["id"],
            "control_key": "independent_security_review",
            "result": "PASS",
            "evidence": {"report": "security-review.pdf"},
            "observed_at": "2026-07-25T12:00:00Z",
        }
        first = self.enterprise.record_attestation(self.actor, payload)
        second = self.enterprise.record_attestation(self.actor, payload)
        self.assertEqual(first["id"], second["id"])
        self.assertTrue(second["duplicate"])
        self.assertTrue(
            self.enterprise.compliance(self.actor, deployment["id"])[
                "enterprise_ready"
            ]
        )

    def test_failed_control_and_region_block_readiness(self):
        self.enterprise.publish_policy(
            self.actor,
            self.policy_payload(controls={"encryption_at_rest": True}),
        )
        deployment = self.enterprise.register_deployment(
            self.actor,
            self.deployment_payload(
                region="EU-CENTRAL",
                data_residency="EU-CENTRAL",
                encryption_at_rest=False,
            ),
        )
        result = self.enterprise.compliance(self.actor, deployment["id"])
        self.assertEqual(result["deployments"][0]["status"], "FAIL")
        self.assertFalse(result["enterprise_ready"])

    def test_workspace_isolation_and_admin_writes(self):
        policy = self.enterprise.publish_policy(self.actor, self.policy_payload())
        other = dict(self.actor)
        other["workspace_id"] = "other-workspace"
        self.assertEqual(self.enterprise.policies(other), [])
        with self.assertRaises(KeyError):
            self.enterprise.policy(other, policy["id"])
        with self.assertRaises(PermissionError):
            self.enterprise.publish_policy(
                self.viewer,
                self.policy_payload(policy_key="viewer-policy"),
            )

    def test_application_routes_and_public_document(self):
        CURRENT_WORKSPACE.set(None)
        app = Phase29Application(
            base=ProductionApplication(create_application(store=self.store))
        )
        public = request(app, "/.well-known/aurora-enterprise.json")
        self.assertEqual(public["json"]["phase"], 29)
        created = request(
            app,
            "/api/platform/enterprise/policies",
            "POST",
            self.policy_payload(),
            self.token,
        )
        self.assertEqual(created["code"], 201)
        CURRENT_WORKSPACE.set(None)
        listed = request(
            app,
            "/api/platform/enterprise/policies",
            token=self.token,
        )
        self.assertEqual(len(listed["json"]["policies"]), 1)


if __name__ == "__main__":
    unittest.main()
