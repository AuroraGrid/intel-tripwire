import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phase26_operations import ProductionOperations
from storage import Store


class PassingQualifier:
    @staticmethod
    def latest(actor):
        return {"status": "PASS", "workspace_id": actor["workspace_id"]}


class Phase26Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "phase26.db")
        _, token = self.store.create_user(
            f"admin-{uuid.uuid4().hex}@example.com", "admin"
        )
        self.actor = self.store.auth(token)
        self.operations = ProductionOperations(
            self.store, PassingQualifier()
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_profiles_cover_public_cloud_and_on_premises_without_ai(self):
        profiles = self.operations.profiles()
        names = {profile["name"] for profile in profiles["profiles"]}
        self.assertEqual(names, {"public", "cloud", "on_premises"})
        self.assertFalse(profiles["ai_policy"]["required"])
        self.assertFalse(
            profiles["ai_policy"]["external_api_enabled_by_default"]
        )

    def test_no_samples_never_self_certifies_uptime(self):
        result = self.operations.slo(self.actor)
        self.assertEqual(result["status"], "NOT_VERIFIED")
        self.assertIsNone(result["uptime_percent"])
        self.assertFalse(
            self.operations.readiness(self.actor)[
                "ready_for_public_claim"
            ]
        )

    def test_samples_are_validated_idempotent_and_measured(self):
        payload = {
            "component": "platform",
            "state": "healthy",
            "latency_ms": 42,
            "freshness_seconds": 5,
            "observed_at": "2026-07-23T06:00:00Z",
        }
        first = self.operations.record_sample(self.actor, payload)
        second = self.operations.record_sample(self.actor, payload)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.operations.events(self.actor)), 1)
        slo = self.operations.slo(self.actor, 24 * 365)
        self.assertEqual(slo["status"], "PASS")
        self.assertEqual(slo["uptime_percent"], 100.0)
        self.assertEqual(slo["p95_latency_ms"], 42.0)

    def test_degraded_sample_fails_slo_instead_of_hiding_it(self):
        self.operations.record_sample(
            self.actor,
            {
                "component": "worker",
                "state": "DEGRADED",
                "observed_at": "2026-07-23T06:01:00Z",
            },
        )
        result = self.operations.slo(self.actor, 24 * 365)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["uptime_percent"], 0.0)

    def test_required_drills_and_slo_enable_public_readiness(self):
        self.operations.record_sample(
            self.actor,
            {
                "component": "platform",
                "state": "HEALTHY",
                "observed_at": "2026-07-23T06:02:00Z",
            },
        )
        for drill_type in (
            "BACKUP_RESTORE",
            "ROLLBACK",
            "DISASTER_RECOVERY",
        ):
            record = self.operations.record_drill(
                self.actor,
                {
                    "profile": "public",
                    "drill_type": drill_type,
                    "state": "PASSED",
                    "performed_at": (
                        "2026-07-23T06:"
                        f"{10 + len(self.operations.events(self.actor)):02d}:00Z"
                    ),
                    "evidence": {"artifact": f"{drill_type}.json"},
                },
            )
            self.assertEqual(record["state"], "PASSED")
        readiness = self.operations.readiness(self.actor)
        self.assertEqual(readiness["status"], "PASS")
        self.assertTrue(readiness["ready_for_public_claim"])

    def test_workspace_isolation_and_admin_mutation(self):
        self.operations.record_sample(
            self.actor,
            {
                "component": "database",
                "state": "HEALTHY",
                "observed_at": "2026-07-23T06:30:00Z",
            },
        )
        other = dict(self.actor)
        other["workspace_id"] = "another-workspace"
        self.assertEqual(self.operations.events(other), [])
        _, viewer_token = self.store.create_user(
            f"viewer-{uuid.uuid4().hex}@example.com", "viewer"
        )
        viewer = self.store.auth(viewer_token)
        with self.assertRaises(PermissionError):
            self.operations.record_sample(
                viewer,
                {"component": "api", "state": "HEALTHY"},
            )

    def test_invalid_metrics_and_drills_are_rejected(self):
        with self.assertRaises(ValueError):
            self.operations.record_sample(
                self.actor,
                {
                    "component": "platform",
                    "state": "HEALTHY",
                    "latency_ms": -1,
                },
            )
        with self.assertRaises(ValueError):
            self.operations.record_drill(
                self.actor,
                {
                    "profile": "public",
                    "drill_type": "PRETEND_SUCCESS",
                    "state": "PASSED",
                },
            )


if __name__ == "__main__":
    unittest.main()
