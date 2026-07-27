from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from identity import CURRENT_WORKSPACE
from phase31_benchmarking import ContinuousBenchmark
from phase31_complete import Phase31Application
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


class Phase31Tests(unittest.TestCase):
    def setUp(self):
        CURRENT_WORKSPACE.set(None)
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "phase31.db")
        _, self.token = self.store.create_user(
            f"phase31-admin-{uuid.uuid4().hex}@example.com", "admin"
        )
        _, self.viewer_token = self.store.create_user(
            f"phase31-viewer-{uuid.uuid4().hex}@example.com", "viewer"
        )
        self.admin = self.store.auth(self.token)
        self.viewer = self.store.auth(self.viewer_token)
        self.engine = ContinuousBenchmark(self.store)

    def tearDown(self):
        CURRENT_WORKSPACE.set(None)
        self.temp.cleanup()

    def target(self):
        return self.engine.upsert_target(
            self.admin,
            {
                "target_key": "world-monitor",
                "name": "World Monitor",
                "category": "OSINT",
                "metadata": {"source": "official"},
            },
        )

    def test_target_requires_admin_and_is_workspace_scoped(self):
        with self.assertRaises(PermissionError):
            self.engine.upsert_target(
                self.viewer,
                {"target_key": "x", "name": "X", "category": "OSINT"},
            )
        target = self.target()
        self.assertEqual(target["target_key"], "world-monitor")
        self.assertEqual(len(self.engine.targets(self.admin)), 1)

    def test_run_is_idempotent_and_requires_evidence(self):
        target = self.target()
        payload = {
            "run_key": "2026-07-27-world-monitor",
            "target_id": target["id"],
            "observations": [
                {
                    "metric_key": "api_operations",
                    "aurora_value": 210,
                    "competitor_value": 190,
                    "tolerance": 0,
                    "direction": "higher",
                    "evidence": {"report": "qualification-2026-07-27"},
                }
            ],
        }
        first = self.engine.create_run(self.admin, payload)
        second = self.engine.create_run(self.admin, payload)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["observations"][0]["result"], "AHEAD")
        with self.assertRaises(ValueError):
            bad = dict(payload)
            bad["run_key"] = "missing-evidence"
            bad["observations"] = [
                {"metric_key": "x", "aurora_value": 1, "competitor_value": 2}
            ]
            self.engine.create_run(self.admin, bad)

    def test_behind_result_creates_alert_and_blocks_superiority(self):
        target = self.target()
        run = self.engine.create_run(
            self.admin,
            {
                "run_key": "regression",
                "target_id": target["id"],
                "observations": [
                    {
                        "metric_key": "public_uptime_days",
                        "aurora_value": 2,
                        "competitor_value": 365,
                        "tolerance": 1,
                        "direction": "higher",
                        "evidence": {"url": "https://example.invalid/benchmark"},
                    }
                ],
            },
        )
        self.assertFalse(run["summary"]["superiority_claim_allowed"])
        self.assertEqual(run["observations"][0]["result"], "BEHIND")
        self.assertEqual(run["alerts"][0]["severity"], "HIGH")

    def test_non_finite_values_rejected(self):
        target = self.target()
        with self.assertRaises(ValueError):
            self.engine.create_run(
                self.admin,
                {
                    "run_key": "nan",
                    "target_id": target["id"],
                    "observations": [
                        {
                            "metric_key": "x",
                            "aurora_value": float("nan"),
                            "competitor_value": 1,
                            "evidence": {"report": "r"},
                        }
                    ],
                },
            )

    def test_application_routes_and_public_document(self):
        CURRENT_WORKSPACE.set(None)
        app = Phase31Application(
            base=ProductionApplication(create_application(store=self.store))
        )
        public = request(app, "/.well-known/aurora-benchmarking.json")
        self.assertEqual(public["code"], 200)
        self.assertEqual(public["json"]["phase"], 31)
        created = request(
            app,
            "/api/platform/benchmarks/targets",
            method="POST",
            token=self.token,
            body={
                "target_key": "world-monitor",
                "name": "World Monitor",
                "category": "OSINT",
            },
        )
        self.assertEqual(created["code"], 201)
        CURRENT_WORKSPACE.set(None)
        run = request(
            app,
            "/api/platform/benchmarks/runs",
            method="POST",
            token=self.token,
            body={
                "run_key": "api-run",
                "target_id": created["json"]["id"],
                "observations": [
                    {
                        "metric_key": "mcp_tools",
                        "aurora_value": 11,
                        "competitor_value": 10,
                        "evidence": {"artifact": "benchmark.json"},
                    }
                ],
            },
        )
        self.assertEqual(run["code"], 201)
        self.assertEqual(run["json"]["status"], "COMPLETE")


if __name__ == "__main__":
    unittest.main()
