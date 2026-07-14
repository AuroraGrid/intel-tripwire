import json
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app
from load_test import run_load
from release_check import validate
from release_engine import Adapter, ReleaseAggregator, canonical_url, collapse, reliability


def record(identifier="e1", family="example.com", title="Major port outage reported", url="https://example.com/story?utm_source=x"):
    item = app.Evidence(identifier, title, url, family, family, 3, "2026-07-14T00:00:00Z", "news_report")
    setattr(item, "source_origin", family)
    return item


class Response:
    status = 200
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self): return json.dumps({"events": []}).encode()


class Phase6Tests(unittest.TestCase):
    def test_canonical_url(self):
        self.assertEqual(canonical_url("https://www.example.com/a/?utm_source=x&z=2#x"), "https://example.com/a?z=2")

    def test_duplicate_lineage(self):
        left, right = record("a", "one.example"), record("b", "two.example")
        setattr(left, "source_origin", "wire-1")
        setattr(right, "source_origin", "wire-1")
        unique, suppressed = collapse([left, right])
        self.assertEqual((len(unique), suppressed), (1, 1))

    def test_reliability(self):
        self.assertGreater(reliability(1, True), reliability(3, False))

    def test_partial_failure(self):
        good = Adapter("Good", "good.example", 1, True, "test", lambda: [record(family="good.example")])
        bad = Adapter("Bad", "bad.example", 3, False, "test", lambda: (_ for _ in ()).throw(RuntimeError("down")))
        with patch.dict(os.environ, {"AURORA_OFFLINE": "0"}, clear=False):
            payload = ReleaseAggregator(adapter_factory=lambda query: [good, bad]).collect()
        self.assertEqual(payload["mode"], "live_degraded")

    def test_single_flight(self):
        calls = {"value": 0}
        lock = threading.Lock()
        def fetch():
            with lock: calls["value"] += 1
            time.sleep(0.05)
            return [record()]
        aggregate = ReleaseAggregator(adapter_factory=lambda query: [Adapter("One", "example.com", 3, False, "test", fetch)])
        with patch.dict(os.environ, {"AURORA_OFFLINE": "0"}, clear=False):
            threads = [threading.Thread(target=aggregate.collect) for _ in range(6)]
            [thread.start() for thread in threads]
            [thread.join() for thread in threads]
        self.assertEqual(calls["value"], 1)

    def test_load_runner(self):
        result = run_load("http://test", requests=12, concurrency=3, opener=lambda *args, **kwargs: Response())
        self.assertEqual(result["success_rate"], 1.0)

    def test_release_validation(self):
        values = {"POSTGRES_PASSWORD":"p"*40,"AURORA_BOOTSTRAP_SECRET":"b"*40,"AURORA_WEBHOOK_SECRET":"w"*40,"AURORA_CORS_ORIGIN":"https://aurora.test","AURORA_ALLOWED_HOSTS":"aurora.test","AURORA_TRUSTED_PROXIES":"172.16.0.0/12","AURORA_REQUIRE_WORKER":"1"}
        self.assertEqual(validate(values), [])


if __name__ == "__main__":
    unittest.main()
