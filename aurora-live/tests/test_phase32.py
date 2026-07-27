import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from identity import CURRENT_WORKSPACE
from phase32_complete import Phase32Application
from phase32_product_spec import CAPABILITIES, gaps, manifest
from platform_wsgi import create_application
from production_wsgi import ProductionApplication
from storage import Store


def request(app, path):
    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": path.split("?", 1)[0],
        "QUERY_STRING": path.split("?", 1)[1] if "?" in path else "",
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "80",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "wsgi.version": (1, 0),
        "wsgi.url_scheme": "http",
        "wsgi.input": io.BytesIO(b""),
        "wsgi.errors": io.StringIO(),
        "wsgi.multithread": False,
        "wsgi.multiprocess": False,
        "wsgi.run_once": False,
        "CONTENT_LENGTH": "0",
        "REMOTE_ADDR": "127.0.0.1",
        "HTTP_HOST": "localhost",
    }
    captured = {}
    def start(status, headers):
        captured["status"] = int(status.split()[0])
    payload = json.loads(b"".join(app(environ, start)).decode())
    return captured["status"], payload


class Phase32Tests(unittest.TestCase):
    def test_manifest_is_canonical_and_complete(self):
        value = manifest()
        self.assertEqual(value["phase"], 32)
        self.assertEqual(value["product"], "AURORA LIVE")
        self.assertGreaterEqual(len(CAPABILITIES), 40)
        self.assertIn("Global Operating Picture", value["interface"])
        self.assertEqual(sum(value["counts"].values()), len(CAPABILITIES))

    def test_p0_gaps_include_original_product_constraints(self):
        value = gaps("P0")
        keys = {item["key"] for item in value["gaps"]}
        self.assertIn("webcams", keys)
        self.assertIn("aviation", keys)
        self.assertIn("maritime", keys)
        self.assertIn("prediction-markets", keys)
        self.assertIn("free-public", keys)

    def test_invalid_priority_is_rejected(self):
        with self.assertRaises(ValueError):
            gaps("P9")

    def test_public_routes(self):
        CURRENT_WORKSPACE.set(None)
        temp = tempfile.TemporaryDirectory()
        try:
            store = Store(Path(temp.name) / "phase32.db")
            app = Phase32Application(base=ProductionApplication(create_application(store=store)))
            status, product = request(app, "/.well-known/aurora-product.json")
            self.assertEqual(status, 200)
            self.assertEqual(product["phase"], 32)
            status, p0 = request(app, "/api/public/product/gaps?priority=P0")
            self.assertEqual(status, 200)
            self.assertEqual(p0["priority"], "P0")
        finally:
            CURRENT_WORKSPACE.set(None)
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()
