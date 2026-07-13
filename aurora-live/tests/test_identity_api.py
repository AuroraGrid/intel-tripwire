import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from identity import CURRENT_WORKSPACE
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
    def start(status, headers): captured.update(status=status, headers=dict(headers))
    result = b"".join(app(environ, start))
    return {"code": int(captured["status"].split()[0]), "json": json.loads(result) if result else None}


class IdentityAPITests(unittest.TestCase):
    def setUp(self):
        CURRENT_WORKSPACE.set(None)
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "api.db")
        self.owner, self.owner_token = self.store.create_user("api-owner@example.com", "admin")
        self.viewer, self.viewer_token = self.store.create_user("api-viewer@example.com", "viewer")
        with patch.dict(os.environ, {"AURORA_ALLOWED_HOSTS": "localhost"}, clear=False):
            self.app = ProductionApplication(create_application(store=self.store))

    def tearDown(self):
        self.temp.cleanup()

    def test_workspace_membership_token_and_audit_routes(self):
        workspaces = request(self.app, "/api/platform/workspaces", token=self.owner_token)
        self.assertEqual(workspaces["code"], 200)
        self.assertEqual(len(workspaces["json"]["workspaces"]), 1)

        created = request(self.app, "/api/platform/workspaces", "POST", {"name": "Investigations"}, self.owner_token)
        self.assertEqual(created["code"], 201)
        workspace_id = created["json"]["id"]

        membership = request(self.app, "/api/platform/memberships", "POST", {"user_id": self.viewer["id"], "role": "analyst", "workspace_id": workspace_id}, self.owner_token)
        self.assertEqual(membership["code"], 201)

        token = request(self.app, "/api/platform/tokens", "POST", {"user_id": self.viewer["id"], "name": "investigations", "workspace_id": workspace_id}, self.owner_token)
        self.assertEqual(token["code"], 201)
        secret = token["json"]["secret"]
        token_id = token["json"]["token"]["id"]

        me = request(self.app, "/api/platform/me", token=secret)
        self.assertEqual(me["code"], 200)
        self.assertEqual(me["json"]["workspace_id"], workspace_id)

        CURRENT_WORKSPACE.set(None)
        owner_context = self.store.auth(self.owner_token)
        self.assertEqual(owner_context["workspace_role"], "owner")
        revoke = request(self.app, f"/api/platform/tokens/{token_id}/revoke", "POST", {}, self.owner_token)
        self.assertEqual(revoke["code"], 404)

        CURRENT_WORKSPACE.set(None)
        second_owner_token_record, second_owner_secret = self.store.identity.issue_token(owner_context, self.owner["id"], "second-owner", workspace_id=workspace_id)
        revoke = request(self.app, f"/api/platform/tokens/{second_owner_token_record['id']}/revoke", "POST", {}, second_owner_secret)
        self.assertEqual(revoke["code"], 200)

        audit = request(self.app, "/api/platform/audit?limit=20", token=self.owner_token)
        self.assertEqual(audit["code"], 200)
        self.assertTrue(audit["json"]["events"])

    def test_viewer_cannot_access_admin_routes(self):
        result = request(self.app, "/api/platform/tokens", token=self.viewer_token)
        self.assertEqual(result["code"], 403)
        self.assertEqual(result["json"]["error"]["code"], "forbidden")


if __name__ == "__main__":
    unittest.main()
