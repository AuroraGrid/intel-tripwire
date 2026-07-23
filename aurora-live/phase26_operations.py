from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any

from storage import now, sid


PROFILES = {
    "public": {
        "purpose": "Internet-facing public intelligence service",
        "entrypoint": "phase26_complete:application",
        "database": "PostgreSQL 16",
        "runtime": "Docker Compose behind automatic HTTPS",
        "manifest": "docker-compose.public.yml",
        "requirements": [
            "public DNS name",
            "TLS termination",
            "persistent PostgreSQL volume",
            "at least one platform and one worker replica",
            "tested backup and restore",
        ],
    },
    "cloud": {
        "purpose": "Portable managed-cloud deployment",
        "entrypoint": "phase26_complete:application",
        "database": "managed PostgreSQL or PostgreSQL 16",
        "runtime": "Kubernetes 1.29+",
        "manifest": "deploy/phase26/kubernetes.yaml",
        "requirements": [
            "Ingress controller and TLS certificate",
            "Kubernetes Secret populated outside source control",
            "persistent database",
            "pod disruption budget",
            "horizontal autoscaling metrics",
        ],
    },
    "on_premises": {
        "purpose": "Customer-controlled or disconnected deployment",
        "entrypoint": "phase26_complete:application",
        "database": "PostgreSQL 16",
        "runtime": "Docker Compose on a controlled host",
        "manifest": "docker-compose.onprem.yml",
        "requirements": [
            "private image or source mirror",
            "local persistent volumes",
            "local backup target",
            "operator-controlled TLS",
            "no external AI dependency",
        ],
    },
}

COMPONENTS = {
    "platform",
    "database",
    "worker",
    "sources",
    "detection",
    "api",
    "backup",
}
SAMPLE_STATES = {"HEALTHY", "DEGRADED", "DOWN"}
DRILL_TYPES = {
    "BACKUP_RESTORE",
    "ROLLBACK",
    "DISASTER_RECOVERY",
    "LOAD_TEST",
    "SECURITY_REVIEW",
    "MOBILE_ACCEPTANCE",
}
DRILL_STATES = {"PENDING", "PASSED", "FAILED"}


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(
        0,
        min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1),
    )
    return round(float(ordered[index]), 3)


class ProductionOperations:
    """Evidence ledger for deployability, SLOs, and operational drills.

    Phase 26 never infers public uptime from a successful process import. A
    deployment becomes operationally verified only after real samples and
    drills have been recorded for the workspace.
    """

    def __init__(self, store, qualifier=None):
        self.store = store
        self.qualifier = qualifier
        self.init()

    def init(self):
        with self.store.db() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS operational_samples(
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    component TEXT NOT NULL,
                    state TEXT NOT NULL,
                    latency_ms REAL,
                    freshness_seconds REAL,
                    details TEXT NOT NULL,
                    actor_user_id TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS operational_drills(
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    drill_type TEXT NOT NULL,
                    state TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    actor_user_id TEXT NOT NULL,
                    performed_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_operational_samples_workspace
                    ON operational_samples(workspace_id,observed_at DESC);
                CREATE INDEX IF NOT EXISTS idx_operational_samples_component
                    ON operational_samples(workspace_id,component,observed_at DESC);
                CREATE INDEX IF NOT EXISTS idx_operational_drills_workspace
                    ON operational_drills(workspace_id,performed_at DESC);
                CREATE INDEX IF NOT EXISTS idx_operational_drills_type
                    ON operational_drills(workspace_id,drill_type,performed_at DESC);
                """
            )

    @staticmethod
    def profiles():
        return {
            "profiles": [
                {"name": name, **profile}
                for name, profile in PROFILES.items()
            ],
            "default": "public",
            "ai_policy": {
                "required": False,
                "default": "deterministic local processing",
                "external_api_enabled_by_default": False,
                "supported_optional_mode": "operator-configured free provider",
                "secret_required_only_when_enabled": True,
            },
        }

    @staticmethod
    def profile(name: str):
        normalized = str(name or "public").strip().lower().replace("-", "_")
        if normalized not in PROFILES:
            raise ValueError("unknown deployment profile")
        return {"name": normalized, **PROFILES[normalized]}

    def record_sample(self, actor, payload: dict[str, Any]):
        self.store.identity.require(actor, "admin")
        component = str(payload.get("component") or "").strip().lower()
        state = str(payload.get("state") or "").strip().upper()
        if component not in COMPONENTS:
            raise ValueError("invalid operational component")
        if state not in SAMPLE_STATES:
            raise ValueError("invalid operational state")
        latency = payload.get("latency_ms")
        freshness = payload.get("freshness_seconds")
        latency = None if latency is None else float(latency)
        freshness = None if freshness is None else float(freshness)
        if latency is not None and not 0 <= latency <= 3_600_000:
            raise ValueError("latency_ms out of range")
        if freshness is not None and not 0 <= freshness <= 31_536_000:
            raise ValueError("freshness_seconds out of range")
        observed = str(payload.get("observed_at") or now())
        _parse_timestamp(observed)
        workspace = actor["workspace_id"]
        sample_id = sid(
            "operational-sample",
            workspace,
            component,
            observed,
            state,
        )
        details = payload.get("details") or {}
        if not isinstance(details, dict):
            raise ValueError("details must be an object")
        with self.store.db() as connection:
            connection.execute(
                """
                INSERT INTO operational_samples(
                    id,workspace_id,component,state,latency_ms,
                    freshness_seconds,details,actor_user_id,observed_at,
                    created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO NOTHING
                """,
                (
                    sample_id,
                    workspace,
                    component,
                    state,
                    latency,
                    freshness,
                    json.dumps(details, sort_keys=True),
                    actor["id"],
                    observed,
                    now(),
                ),
            )
        self.store.identity.audit(
            workspace,
            actor["id"],
            "operations.sample.recorded",
            "operational_sample",
            sample_id,
            metadata={"component": component, "state": state},
        )
        return self.sample(actor, sample_id)

    def sample(self, actor, sample_id: str):
        with self.store.db() as connection:
            row = connection.execute(
                """
                SELECT * FROM operational_samples
                WHERE workspace_id=? AND id=?
                """,
                (actor["workspace_id"], sample_id),
            ).fetchone()
        if not row:
            raise KeyError("operational sample not found")
        item = dict(row)
        item["details"] = json.loads(item["details"])
        return item

    def record_drill(self, actor, payload: dict[str, Any]):
        self.store.identity.require(actor, "admin")
        profile = str(payload.get("profile") or "public").lower()
        self.profile(profile)
        drill_type = str(payload.get("drill_type") or "").upper()
        state = str(payload.get("state") or "").upper()
        if drill_type not in DRILL_TYPES:
            raise ValueError("invalid drill type")
        if state not in DRILL_STATES:
            raise ValueError("invalid drill state")
        evidence = payload.get("evidence") or {}
        if not isinstance(evidence, dict):
            raise ValueError("evidence must be an object")
        performed = str(payload.get("performed_at") or now())
        _parse_timestamp(performed)
        workspace = actor["workspace_id"]
        drill_id = sid(
            "operational-drill",
            workspace,
            profile,
            drill_type,
            performed,
        )
        with self.store.db() as connection:
            connection.execute(
                """
                INSERT INTO operational_drills(
                    id,workspace_id,profile,drill_type,state,evidence,
                    actor_user_id,performed_at,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO NOTHING
                """,
                (
                    drill_id,
                    workspace,
                    profile,
                    drill_type,
                    state,
                    json.dumps(evidence, sort_keys=True),
                    actor["id"],
                    performed,
                    now(),
                ),
            )
        self.store.identity.audit(
            workspace,
            actor["id"],
            "operations.drill.recorded",
            "operational_drill",
            drill_id,
            metadata={
                "profile": profile,
                "drill_type": drill_type,
                "state": state,
            },
        )
        return self.drill(actor, drill_id)

    def drill(self, actor, drill_id: str):
        with self.store.db() as connection:
            row = connection.execute(
                """
                SELECT * FROM operational_drills
                WHERE workspace_id=? AND id=?
                """,
                (actor["workspace_id"], drill_id),
            ).fetchone()
        if not row:
            raise KeyError("operational drill not found")
        item = dict(row)
        item["evidence"] = json.loads(item["evidence"])
        return item

    def events(self, actor, limit=100):
        limit = max(1, min(500, int(limit)))
        with self.store.db() as connection:
            samples = [
                {**dict(row), "record_type": "SAMPLE"}
                for row in connection.execute(
                    """
                    SELECT * FROM operational_samples
                    WHERE workspace_id=?
                    ORDER BY observed_at DESC LIMIT ?
                    """,
                    (actor["workspace_id"], limit),
                ).fetchall()
            ]
            drills = [
                {**dict(row), "record_type": "DRILL"}
                for row in connection.execute(
                    """
                    SELECT * FROM operational_drills
                    WHERE workspace_id=?
                    ORDER BY performed_at DESC LIMIT ?
                    """,
                    (actor["workspace_id"], limit),
                ).fetchall()
            ]
        for item in samples:
            item["details"] = json.loads(item["details"])
        for item in drills:
            item["evidence"] = json.loads(item["evidence"])
        return sorted(
            samples + drills,
            key=lambda item: item.get("observed_at")
            or item.get("performed_at")
            or "",
            reverse=True,
        )[:limit]

    def slo(self, actor, window_hours=24):
        window_hours = max(1, min(24 * 90, int(window_hours)))
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=window_hours)
        ).isoformat().replace("+00:00", "Z")
        with self.store.db() as connection:
            rows = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT * FROM operational_samples
                    WHERE workspace_id=? AND observed_at>=?
                    ORDER BY observed_at
                    """,
                    (actor["workspace_id"], cutoff),
                ).fetchall()
            ]
        total = len(rows)
        healthy = sum(row["state"] == "HEALTHY" for row in rows)
        uptime = round(healthy * 100 / total, 4) if total else None
        latencies = [
            float(row["latency_ms"])
            for row in rows
            if row["latency_ms"] is not None
        ]
        freshness = [
            float(row["freshness_seconds"])
            for row in rows
            if row["freshness_seconds"] is not None
        ]
        return {
            "window_hours": window_hours,
            "samples": total,
            "uptime_percent": uptime,
            "p95_latency_ms": _percentile(latencies, 0.95),
            "p95_freshness_seconds": _percentile(freshness, 0.95),
            "targets": {
                "uptime_percent": 99.9,
                "high_priority_detection_seconds": 60,
                "normal_detection_seconds": 300,
            },
            "status": (
                "PASS"
                if uptime is not None and uptime >= 99.9
                else "FAIL"
                if uptime is not None
                else "NOT_VERIFIED"
            ),
            "note": "No samples means not verified, never a pass.",
        }

    def readiness(self, actor, profile="public"):
        profile_record = self.profile(profile)
        slo = self.slo(actor)
        with self.store.db() as connection:
            passed_drills = {
                row["drill_type"]
                for row in connection.execute(
                    """
                    SELECT drill_type FROM operational_drills
                    WHERE workspace_id=? AND profile=? AND state='PASSED'
                    """,
                    (actor["workspace_id"], profile_record["name"]),
                ).fetchall()
            }
            connection.execute("SELECT 1").fetchone()
        required = {"BACKUP_RESTORE", "ROLLBACK", "DISASTER_RECOVERY"}
        checks = [
            {"name": "database_connection", "status": "PASS"},
            {
                "name": "operational_slo",
                "status": slo["status"],
            },
            {
                "name": "backup_restore_drill",
                "status": (
                    "PASS"
                    if "BACKUP_RESTORE" in passed_drills
                    else "NOT_VERIFIED"
                ),
            },
            {
                "name": "rollback_drill",
                "status": (
                    "PASS"
                    if "ROLLBACK" in passed_drills
                    else "NOT_VERIFIED"
                ),
            },
            {
                "name": "disaster_recovery_drill",
                "status": (
                    "PASS"
                    if "DISASTER_RECOVERY" in passed_drills
                    else "NOT_VERIFIED"
                ),
            },
        ]
        if self.qualifier:
            integration = self.qualifier.latest(actor)
            checks.append(
                {
                    "name": "phase25_integration",
                    "status": (
                        "PASS"
                        if integration.get("status") == "PASS"
                        else "NOT_VERIFIED"
                    ),
                }
            )
        complete = (
            slo["status"] == "PASS"
            and required.issubset(passed_drills)
            and all(
                check["status"] == "PASS"
                for check in checks
                if check["name"] == "phase25_integration"
            )
        )
        return {
            "profile": profile_record,
            "checks": checks,
            "ready_for_public_claim": complete,
            "status": "PASS" if complete else "NOT_VERIFIED",
            "slo": slo,
            "policy": {
                "configuration_is_not_operational_proof": True,
                "external_security_review_is_separate": True,
                "ai_api_required": False,
            },
        }
