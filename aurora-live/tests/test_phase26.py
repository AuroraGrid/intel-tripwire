import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phase26_operations import (
    PROFILE_REQUIRED_DRILLS,
    SLO_REQUIRED_COMPONENTS,
    ProductionOperations,
)
from storage import Store


class PassingQualifier:
    @staticmethod
    def latest(actor):
        return {
            "status": "PASS",
            "run_id": "qualification-test-run",
            "workspace_id": actor["workspace_id"],
        }


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
        self.reference = datetime.now(timezone.utc).replace(
            microsecond=0
        ) - timedelta(minutes=5)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def stamp(value):
        return value.astimezone(timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )

    def record_dense_samples(self, state="HEALTHY"):
        start = self.reference - timedelta(hours=24)
        for component in sorted(SLO_REQUIRED_COMPONENTS):
            for index in range(24):
                observed = start + timedelta(
                    minutes=30, hours=index
                )
                self.operations.record_sample(
                    self.actor,
                    {
                        "component": component,
                        "state": state,
                        "latency_ms": 25,
                        "freshness_seconds": 10,
                        "observed_at": self.stamp(observed),
                    },
                )

    def record_required_drills(self, profile="public"):
        for index, drill_type in enumerate(
            sorted(PROFILE_REQUIRED_DRILLS[profile])
        ):
            evidence = {
                "artifact": f"{drill_type.lower()}.json",
                "sha256": f"sha-{index}",
            }
            if drill_type == "SECURITY_REVIEW":
                evidence.update(
                    {
                        "independent": True,
                        "reviewer": "External Security Lab",
                    }
                )
            self.operations.record_drill(
                self.actor,
                {
                    "profile": profile,
                    "drill_type": drill_type,
                    "state": "PASSED",
                    "performed_at": self.stamp(
                        self.reference - timedelta(minutes=index)
                    ),
                    "evidence": evidence,
                },
            )

    def test_profiles_cover_supported_modes_without_ai(self):
        profiles = self.operations.profiles()
        names = {profile["name"] for profile in profiles["profiles"]}
        self.assertEqual(names, {"public", "cloud", "on_premises"})
        self.assertFalse(profiles["ai_policy"]["required"])
        self.assertFalse(
            profiles["ai_policy"]["external_api_enabled_by_default"]
        )

    def test_no_samples_never_self_certifies_uptime(self):
        result = self.operations.slo(
            self.actor,
            as_of=self.stamp(self.reference),
        )
        self.assertEqual(result["status"], "NOT_VERIFIED")
        self.assertIsNone(result["uptime_percent"])
        self.assertFalse(result["coverage"]["complete"])

    def test_one_sample_is_measurement_but_not_slo_evidence(self):
        self.operations.record_sample(
            self.actor,
            {
                "component": "platform",
                "state": "HEALTHY",
                "observed_at": self.stamp(self.reference),
            },
        )
        result = self.operations.slo(
            self.actor,
            as_of=self.stamp(self.reference),
        )
        self.assertEqual(result["status"], "NOT_VERIFIED")
        self.assertFalse(result["coverage"]["complete"])

    def test_dense_component_coverage_can_pass_slo(self):
        self.record_dense_samples()
        result = self.operations.slo(
            self.actor,
            as_of=self.stamp(self.reference),
        )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["uptime_percent"], 100.0)
        self.assertTrue(result["coverage"]["complete"])
        self.assertEqual(
            set(result["coverage"]["components"]),
            SLO_REQUIRED_COMPONENTS,
        )

    def test_degraded_component_fails_complete_slo(self):
        self.record_dense_samples()
        self.operations.record_sample(
            self.actor,
            {
                "component": "worker",
                "state": "DEGRADED",
                "observed_at": self.stamp(
                    self.reference - timedelta(minutes=10)
                ),
            },
        )
        result = self.operations.slo(
            self.actor,
            as_of=self.stamp(self.reference),
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertLess(result["uptime_percent"], 99.9)

    def test_timestamp_offsets_are_normalized_to_utc(self):
        instant = self.reference - timedelta(hours=2)
        offset = instant.astimezone(timezone(timedelta(hours=1)))
        record = self.operations.record_sample(
            self.actor,
            {
                "component": "api",
                "state": "HEALTHY",
                "observed_at": offset.isoformat(),
            },
        )
        self.assertEqual(record["observed_at"], self.stamp(instant))

    def test_profile_alias_is_stored_in_canonical_form(self):
        record = self.operations.record_drill(
            self.actor,
            {
                "profile": "on-premises",
                "drill_type": "BACKUP_RESTORE",
                "state": "PASSED",
                "performed_at": self.stamp(self.reference),
                "evidence": {"artifact": "restore.json"},
            },
        )
        self.assertEqual(record["profile"], "on_premises")

    def test_passed_drills_require_evidence(self):
        with self.assertRaises(ValueError):
            self.operations.record_drill(
                self.actor,
                {
                    "profile": "public",
                    "drill_type": "ROLLBACK",
                    "state": "PASSED",
                    "performed_at": self.stamp(self.reference),
                    "evidence": {},
                },
            )
        with self.assertRaises(ValueError):
            self.operations.record_drill(
                self.actor,
                {
                    "profile": "public",
                    "drill_type": "SECURITY_REVIEW",
                    "state": "PASSED",
                    "performed_at": self.stamp(self.reference),
                    "evidence": {"artifact": "security.json"},
                },
            )

    def test_latest_failed_drill_overrides_older_pass(self):
        self.record_dense_samples()
        self.record_required_drills()
        self.operations.record_drill(
            self.actor,
            {
                "profile": "public",
                "drill_type": "ROLLBACK",
                "state": "FAILED",
                "performed_at": self.stamp(
                    self.reference + timedelta(minutes=1)
                ),
                "evidence": {"artifact": "rollback-failure.json"},
            },
        )
        readiness = self.operations.readiness(
            self.actor,
            as_of=self.stamp(self.reference + timedelta(minutes=2)),
        )
        checks = {item["name"]: item for item in readiness["checks"]}
        self.assertEqual(readiness["status"], "FAIL")
        self.assertEqual(checks["rollback_drill"]["status"], "FAIL")

    def test_complete_current_evidence_enables_public_readiness(self):
        self.record_dense_samples()
        self.record_required_drills()
        readiness = self.operations.readiness(
            self.actor,
            as_of=self.stamp(self.reference),
        )
        self.assertEqual(readiness["status"], "PASS")
        self.assertTrue(readiness["ready_for_public_claim"])
        self.assertTrue(readiness["ready_for_deployment_claim"])

    def test_workspace_isolation_and_admin_mutation(self):
        self.operations.record_sample(
            self.actor,
            {
                "component": "database",
                "state": "HEALTHY",
                "observed_at": self.stamp(self.reference),
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

    def test_invalid_metrics_and_future_times_are_rejected(self):
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
            self.operations.record_sample(
                self.actor,
                {
                    "component": "platform",
                    "state": "HEALTHY",
                    "observed_at": self.stamp(
                        datetime.now(timezone.utc) + timedelta(hours=1)
                    ),
                },
            )


if __name__ == "__main__":
    unittest.main()
