import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phase27_competitive import CompetitiveGapClosure
from storage import Store


class FakeQualifier:
    def benchmark(self, actor):
        return {
            "matrix": [
                {
                    "capability": "Live monitoring breadth",
                    "aurora": {"registered_sensors": 12},
                    "world_monitor": {
                        "external_sources": 65,
                        "curated_feeds": 500,
                    },
                    "result": "NOT_VERIFIED",
                    "reason": "live operation is not yet proven",
                },
                {
                    "capability": "Documented API operations",
                    "aurora": 40,
                    "world_monitor": 190,
                    "result": "BEHIND",
                    "reason": "operation count remains below baseline",
                },
                {
                    "capability": "Route and scenario usability",
                    "aurora": "implemented",
                    "world_monitor": ["Route Explorer", "Scenario Engine"],
                    "result": "NOT_VERIFIED",
                    "reason": "no timed comparison exists",
                },
                {
                    "capability": "Public uptime and battle testing",
                    "aurora": "qualification pending",
                    "world_monitor": "public operating product",
                    "result": "BEHIND",
                    "reason": "sustained public history is missing",
                },
            ]
        }


class Phase27Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.store = Store(Path(cls.temp.name) / "phase27.db")
        _, token = cls.store.create_user(
            f"admin-{uuid.uuid4().hex}@example.com", "admin"
        )
        cls.base_actor = cls.store.auth(token)
        _, viewer_token = cls.store.create_user(
            f"viewer-{uuid.uuid4().hex}@example.com", "viewer"
        )
        cls.viewer_actor = cls.store.auth(viewer_token)
        cls.closure = CompetitiveGapClosure(cls.store, FakeQualifier())

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def actor(self, suffix=None):
        actor = dict(self.base_actor)
        actor["workspace_id"] = (
            suffix or f"phase27-{self._testMethodName}"
        )
        return actor

    @staticmethod
    def stamp(value):
        return value.astimezone(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )

    def test_sync_creates_evidence_gated_rows(self):
        actor = self.actor()
        summary = self.closure.sync(actor)
        self.assertEqual(len(summary["gaps"]), 4)
        self.assertIn(
            "public-uptime-and-battle-testing",
            summary["strategic_open"],
        )
        self.assertFalse(summary["superiority_claim_candidate"])

    def test_live_breadth_closes_only_with_threshold_evidence(self):
        actor = self.actor()
        self.closure.sync(actor)
        self.closure.record_evidence(
            actor,
            {
                "capability": "live-monitoring-breadth",
                "result": "PARITY",
                "source": "live qualification run",
                "metrics": {
                    "external_sources": 65,
                    "curated_feeds": 500,
                    "window_hours": 24,
                    "success_percent": 99.0,
                },
                "evidence": {
                    "artifact": "live-qualification.json",
                    "sha256": "abc123",
                },
            },
        )
        gap = {
            item["slug"]: item for item in self.closure.gaps(actor)
        }["live-monitoring-breadth"]
        self.assertEqual(gap["current_result"], "PARITY")
        self.assertTrue(gap["closed"])

    def test_latest_failure_overrides_older_success(self):
        actor = self.actor()
        self.closure.sync(actor)
        first = datetime.now(timezone.utc) - timedelta(hours=2)
        base = {
            "capability": "documented-api-operations",
            "source": "OpenAPI conformance",
            "metrics": {"aurora": 200, "world_monitor": 190},
            "evidence": {"run_id": "openapi-1"},
        }
        self.closure.record_evidence(
            actor,
            {
                **base,
                "result": "AHEAD",
                "observed_at": self.stamp(first),
            },
        )
        self.closure.record_evidence(
            actor,
            {
                **base,
                "result": "BEHIND",
                "metrics": {"aurora": 180, "world_monitor": 190},
                "evidence": {"run_id": "openapi-2"},
                "observed_at": self.stamp(first + timedelta(hours=1)),
            },
        )
        gap = {
            item["slug"]: item for item in self.closure.gaps(actor)
        }["documented-api-operations"]
        self.assertEqual(gap["current_result"], "BEHIND")
        self.assertFalse(gap["closed"])

    def test_external_closure_requires_independent_evidence(self):
        actor = self.actor()
        self.closure.sync(actor)
        payload = {
            "capability": "public-uptime-and-battle-testing",
            "result": "PARITY",
            "source": "internal dashboard",
            "metrics": {"uptime_percent": 99.99, "window_days": 30},
            "evidence": {"report": "uptime.json"},
        }
        with self.assertRaises(ValueError):
            self.closure.record_evidence(actor, payload)
        record = self.closure.record_evidence(
            actor,
            {
                **payload,
                "source": "Independent Observatory",
                "independent": True,
            },
        )
        self.assertTrue(record["independent"])

    def test_expired_evidence_returns_to_not_verified(self):
        actor = self.actor()
        self.closure.sync(actor)
        observed = datetime.now(timezone.utc) - timedelta(days=10)
        self.closure.record_evidence(
            actor,
            {
                "capability": "live-monitoring-breadth",
                "result": "PARITY",
                "source": "expired qualification",
                "metrics": {
                    "external_sources": 65,
                    "curated_feeds": 500,
                    "window_hours": 24,
                    "success_percent": 99.0,
                },
                "evidence": {"artifact": "old.json"},
                "observed_at": self.stamp(observed),
            },
        )
        gap = {
            item["slug"]: item for item in self.closure.gaps(actor)
        }["live-monitoring-breadth"]
        self.assertEqual(gap["current_result"], "NOT_VERIFIED")
        self.assertIn("expired", gap["current_reason"])

    def test_successful_baseline_requires_fresh_phase27_evidence(self):
        result, reason = self.closure._criteria_result(
            {"baseline_result": "AHEAD"},
            None,
            datetime.now(timezone.utc),
        )
        self.assertEqual(result, "NOT_VERIFIED")
        self.assertIn("fresh Phase 27 evidence", reason)

    def test_workspace_isolation_and_admin_writes(self):
        actor = self.actor("phase27-a")
        other = self.actor("phase27-b")
        self.closure.sync(actor)
        self.assertEqual(self.closure.gaps(other), [])
        viewer = dict(self.viewer_actor)
        viewer["workspace_id"] = actor["workspace_id"]
        with self.assertRaises(PermissionError):
            self.closure.sync(viewer)

    def test_evidence_requires_reference_and_finite_metrics(self):
        actor = self.actor()
        self.closure.sync(actor)
        with self.assertRaises(ValueError):
            self.closure.record_evidence(
                actor,
                {
                    "capability": "documented-api-operations",
                    "result": "PARITY",
                    "source": "bad run",
                    "metrics": {"aurora": float("nan")},
                    "evidence": {},
                },
            )


if __name__ == "__main__":
    unittest.main()
