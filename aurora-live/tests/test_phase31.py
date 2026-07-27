from __future__ import annotations

import io
import json
import os
import tempfile
import unittest

from phase31_benchmarking import ContinuousBenchmark
from phase31_complete import Phase31Application
from platform_wsgi import ProductionApplication
from app import create_application


class Phase31Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.temp.name, "phase31.db")
        self.core = create_application(database_url=self.db)
        self.store = self.core.store
        self.admin, self.token = self.store.create_user("phase31-admin@example.com", "admin")
        self.viewer, self.viewer_token = self.store.create_user("phase31-viewer@example.com", "viewer")
        self.engine = ContinuousBenchmark(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def target(self):
        return self.engine.upsert_target(
            self.admin,
            {"target_key": "world-monitor", "name": "World Monitor", "category": "OSINT", "metadata": {"source": "official"}},
        )

    def test_target_requires_admin_and_is_workspace_scoped(self):
        with self.assertRaises(PermissionError):
            self.engine.upsert_target(self.viewer, {"target_key": "x", "name": "X", "category": "OSINT"})
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
            bad["observations"] = [{"metric_key": "x", "aurora_value": 1, "competitor_value": 2}]
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
                    "observations": [{"metric_key": "x", "aurora_value": float("nan"), "competitor_value": 1, "evidence": {"report": "r"}}],
                },
            )

    def request(self, app, method, path, token="", payload=None):
        body = json.dumps(payload or {}).encode()
        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "QUERY_STRING": "",
            "wsgi.input": io.BytesIO(body),
            "CONTENT_LENGTH": str(len(body)),
            "CONTENT_TYPE": "application/json",
            "HTTP_AUTHORIZATION": f"Bearer {token}" if token else "",
        }
        result = {}
        def start_response(status, headers):
            result["status"] = int(status.split()[0])
        chunks = app(environ, start_response)
        return result["status"], json.loads(b"".join(chunks).decode())

    def test_application_routes_and_public_document(self):
        app = Phase31Application(base=ProductionApplication(self.core))
        status, document = self.request(app, "GET", "/.well-known/aurora-benchmarking.json")
        self.assertEqual(status, 200)
        self.assertEqual(document["phase"], 31)
        status, target = self.request(
            app,
            "POST",
            "/api/platform/benchmarks/targets",
            self.token,
            {"target_key": "world-monitor", "name": "World Monitor", "category": "OSINT"},
        )
        self.assertEqual(status, 201)
        status, run = self.request(
            app,
            "POST",
            "/api/platform/benchmarks/runs",
            self.token,
            {
                "run_key": "api-run",
                "target_id": target["id"],
                "observations": [{"metric_key": "mcp_tools", "aurora_value": 11, "competitor_value": 10, "evidence": {"artifact": "benchmark.json"}}],
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(run["status"], "COMPLETE")


if __name__ == "__main__":
    unittest.main()
