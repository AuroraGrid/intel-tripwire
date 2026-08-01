import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from observability import METRICS
from platform_wsgi import create_application
from production_wsgi import ProductionApplication
from storage import Store


def request(app, path, method="GET", headers=None):
    environ = {"REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": "", "SERVER_NAME": "localhost", "SERVER_PORT": "80", "SERVER_PROTOCOL": "HTTP/1.1", "wsgi.version": (1, 0), "wsgi.url_scheme": "http", "wsgi.input": io.BytesIO(b""), "wsgi.errors": io.StringIO(), "wsgi.multithread": False, "wsgi.multiprocess": False, "wsgi.run_once": False, "CONTENT_LENGTH": "0", "REMOTE_ADDR": "127.0.0.1", "HTTP_HOST": "localhost"}
    for key, value in (headers or {}).items(): environ["HTTP_" + key.upper().replace("-", "_")] = value
    captured = {}
    def start(status, values, exc_info=None): captured.update(status=status, headers=dict(values))
    captured["body"] = b"".join(app(environ, start))
    captured["code"] = int(captured["status"].split()[0])
    return captured


class ObservabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        store = Store(Path(self.temp.name) / "observability.db")
        self.app = ProductionApplication(create_application(store=store))

    def tearDown(self): self.temp.cleanup()

    def test_metrics_and_trace_headers(self):
        live = request(self.app, "/api/platform/live", headers={"X-Trace-ID": "trace-123"})
        self.assertEqual(live["code"], 200)
        self.assertEqual(live["headers"]["X-Trace-ID"], "trace-123")
        metrics = request(self.app, "/api/platform/metrics")
        self.assertEqual(metrics["code"], 200)
        self.assertIn(b"aurora_http_requests_total", metrics["body"])

    def test_readiness_has_diagnostics(self):
        result = request(self.app, "/api/platform/ready")
        self.assertEqual(result["code"], 200)
        data = json.loads(result["body"])
        self.assertTrue(data["checks"]["database"]["ok"])
        self.assertIn("workers", data["checks"])
        self.assertIn("ingestion", data["checks"])

    def test_readiness_requires_worker_and_ingestion_when_enabled(self):
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {"AURORA_REQUIRE_WORKER": "1", "AURORA_REQUIRE_INGESTION": "1", "AURORA_INGESTION_GRACE_SECONDS": "0"}, clear=False):
            result = request(self.app, "/api/platform/ready")
        self.assertEqual(result["code"], 503)
        data = json.loads(result["body"])
        self.assertEqual(data["status"], "not_ready")
        self.assertFalse(data["checks"]["workers"]["ok"])

    def test_metrics_render_counter(self):
        METRICS.inc("aurora_test_total", source="unit")
        self.assertIn(b'aurora_test_total{source="unit"}', METRICS.render())


if __name__ == "__main__": unittest.main()
