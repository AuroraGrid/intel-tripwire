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
SLO_REQUIRED_COMPONENTS = {
    "platform",
    "database",
    "worker",
    "sources",
    "detection",
    "api",
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
PROFILE_REQUIRED_DRILLS = {
    "public": {
        "BACKUP_RESTORE",
        "ROLLBACK",
        "DISASTER_RECOVERY",
        "LOAD_TEST",
        "SECURITY_REVIEW",
        "MOBILE_ACCEPTANCE",
    },
    "cloud": {
        "BACKUP_RESTORE",
        "ROLLBACK",
        "DISASTER_RECOVERY",
        "LOAD_TEST",
        "SECURITY_REVIEW",
    },
    "on_premises": {
        "BACKUP_RESTORE",
        "ROLLBACK",
        "DISASTER_RECOVERY",
        "SECURITY_REVIEW",
    },
}
DRILL_MAX_AGE_DAYS = {
    "BACKUP_RESTORE": 30,
    "ROLLBACK": 90,
    "DISASTER_RECOVERY": 180,
    "LOAD_TEST": 90,
    "SECURITY_REVIEW": 365,
    "MOBILE_ACCEPTANCE": 180,
}
SLO_SAMPLE_INTERVAL_SECONDS = 3600
SLO_MAX_GAP_SECONDS = 5400
MAX_FUTURE_SKEW_SECONDS = 300
EVIDENCE_REFERENCE_KEYS = {
    "artifact",
    "url",
    "report",
    "report_url",
    "run_id",
    "checksum",
    "sha256",
}


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _reject_future_timestamp(value: datetime, field: str) -> None:
    ceiling = datetime.now(timezone.utc) + timedelta(
        seconds=MAX_FUTURE_SKEW_SECONDS
    )
    if value > ceiling:
        raise ValueError(f"{field} is too far in the future")


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(
        0,
        min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1),
    )
    return round(float(ordered[index]), 3)


def _evidence_is_usable(drill_type: str, evidence: dict[str, Any]) -> bool:
    if not evidence or not any(
        evidence.get(key) not in (None, "", [], {})
        for key in EVIDENCE_REFERENCE_KEYS
    ):
        return False
    if drill_type == "SECURITY_REVIEW":
        return bool(evidence.get("independent")) and bool(
            str(evidence.get("reviewer") or "").strip()
        )
    return True


class ProductionOperations:
    """Evidence ledger for deployability, SLOs, and operational drills.

    Phase 26 never infers public uptime from a successful process import. A
    deployment becomes operationally verified only after real, sufficiently
    dense samples and current drills have been recorded for the workspace.
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
        observed_value = str(payload.get("observed_at") or now())
        observed_dt = _parse_timestamp(observed_value)
        _reject_future_timestamp(observed_dt, "observed_at")
        observed = _format_timestamp(observed_dt)
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
        profile_record = self.profile(payload.get("profile") or "public")
        profile = profile_record["name"]
        drill_type = str(payload.get("drill_type") or "").strip().upper()
        state = str(payload.get("state") or "").strip().upper()
        if drill_type not in DRILL_TYPES:
            raise ValueError("invalid drill type")
        if state not in DRILL_STATES:
            raise ValueError("invalid drill state")
        evidence = payload.get("evidence") or {}
        if not isinstance(evidence, dict):
            raise ValueError("evidence must be an object")
        if state == "PASSED" and not _evidence_is_usable(
            drill_type, evidence
        ):
            raise ValueError("passed drill requires verifiable evidence")
        performed_value = str(payload.get("performed_at") or now())
        performed_dt = _parse_timestamp(performed_value)
        _reject_future_timestamp(performed_dt, "performed_at")
        performed = _format_timestamp(performed_dt)
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

    def slo(
        self,
        actor,
        window_hours=24,
        required_components=None,
        as_of=None,
    ):
        window_hours = max(1, min(24 * 90, int(window_hours)))
        end = _parse_timestamp(str(as_of or now()))
        start = end - timedelta(hours=window_hours)
        start_text = _format_timestamp(start)
        end_text = _format_timestamp(end)
        required = set(required_components or SLO_REQUIRED_COMPONENTS)
        unknown = required - COMPONENTS
        if unknown:
            raise ValueError("invalid required operational component")
        with self.store.db() as connection:
            rows = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT * FROM operational_samples
                    WHERE workspace_id=? AND observed_at>=? AND observed_at<=?
                    ORDER BY observed_at
                    """,
                    (actor["workspace_id"], start_text, end_text),
                ).fetchall()
            ]
        selected = [row for row in rows if row["component"] in required]
        expected_per_component = max(
            2,
            math.ceil(
                window_hours * 3600 / SLO_SAMPLE_INTERVAL_SECONDS
            ),
        )
        component_results = {}
        component_uptimes = []
        coverage_complete = True
        for component in sorted(required):
            component_rows = [
                row for row in selected if row["component"] == component
            ]
            timestamps = [
                _parse_timestamp(row["observed_at"])
                for row in component_rows
            ]
            boundary_points = [start, *timestamps, end]
            max_gap = max(
                (
                    (right - left).total_seconds()
                    for left, right in zip(
                        boundary_points, boundary_points[1:]
                    )
                ),
                default=window_hours * 3600,
            )
            count = len(component_rows)
            healthy = sum(
                row["state"] == "HEALTHY" for row in component_rows
            )
            component_uptime = (
                round(healthy * 100 / count, 4) if count else None
            )
            if component_uptime is not None:
                component_uptimes.append(component_uptime)
            complete = (
                count >= expected_per_component
                and max_gap <= SLO_MAX_GAP_SECONDS
            )
            coverage_complete = coverage_complete and complete
            component_results[component] = {
                "samples": count,
                "healthy_samples": healthy,
                "uptime_percent": component_uptime,
                "max_gap_seconds": round(max_gap, 3),
                "coverage_complete": complete,
            }
        uptime = (
            min(component_uptimes)
            if len(component_uptimes) == len(required)
            else None
        )
        latencies = [
            float(row["latency_ms"])
            for row in selected
            if row["latency_ms"] is not None
        ]
        freshness = [
            float(row["freshness_seconds"])
            for row in selected
            if row["freshness_seconds"] is not None
        ]
        measurement_status = (
            "PASS"
            if uptime is not None and uptime >= 99.9
            else "FAIL"
            if uptime is not None
            else "NOT_VERIFIED"
        )
        status = measurement_status if coverage_complete else "NOT_VERIFIED"
        return {
            "window_hours": window_hours,
            "window_start": start_text,
            "window_end": end_text,
            "samples": len(selected),
            "uptime_percent": uptime,
            "p95_latency_ms": _percentile(latencies, 0.95),
            "p95_freshness_seconds": _percentile(freshness, 0.95),
            "targets": {
                "uptime_percent": 99.9,
                "high_priority_detection_seconds": 60,
                "normal_detection_seconds": 300,
                "sample_interval_seconds": SLO_SAMPLE_INTERVAL_SECONDS,
                "maximum_gap_seconds": SLO_MAX_GAP_SECONDS,
            },
            "coverage": {
                "required_components": sorted(required),
                "expected_samples_per_component": expected_per_component,
                "complete": coverage_complete,
                "components": component_results,
            },
            "measurement_status": measurement_status,
            "status": status,
            "note": (
                "A passing uptime percentage is not sufficient without "
                "complete component and time-window coverage."
            ),
        }

    def _latest_drills(self, actor, profile: str):
        with self.store.db() as connection:
            rows = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT * FROM operational_drills
                    WHERE workspace_id=? AND profile=?
                    ORDER BY performed_at DESC, created_at DESC
                    """,
                    (actor["workspace_id"], profile),
                ).fetchall()
            ]
        latest = {}
        for row in rows:
            latest.setdefault(row["drill_type"], row)
        for item in latest.values():
            item["evidence"] = json.loads(item["evidence"])
        return latest

    @staticmethod
    def _drill_check(drill_type: str, record, as_of: datetime):
        check = {
            "name": f"{drill_type.lower()}_drill",
            "drill_type": drill_type,
            "status": "NOT_VERIFIED",
            "max_age_days": DRILL_MAX_AGE_DAYS[drill_type],
        }
        if not record:
            check["reason"] = "no drill recorded"
            return check
        performed = _parse_timestamp(record["performed_at"])
        age = max(0.0, (as_of - performed).total_seconds() / 86400)
        check.update(
            {
                "record_id": record["id"],
                "state": record["state"],
                "performed_at": record["performed_at"],
                "age_days": round(age, 3),
            }
        )
        if record["state"] == "FAILED":
            check["status"] = "FAIL"
            check["reason"] = "latest drill failed"
        elif record["state"] != "PASSED":
            check["reason"] = "latest drill is not passed"
        elif age > DRILL_MAX_AGE_DAYS[drill_type]:
            check["reason"] = "latest passing drill is expired"
        elif not _evidence_is_usable(drill_type, record["evidence"]):
            check["reason"] = "drill evidence is incomplete"
        else:
            check["status"] = "PASS"
            check["reason"] = "latest drill passed with current evidence"
        return check

    def readiness(self, actor, profile="public", as_of=None):
        profile_record = self.profile(profile)
        reference_time = _parse_timestamp(str(as_of or now()))
        slo = self.slo(actor, 24, as_of=_format_timestamp(reference_time))
        try:
            with self.store.db() as connection:
                connection.execute("SELECT 1").fetchone()
            database_status = "PASS"
        except Exception:
            database_status = "FAIL"
        required_drills = PROFILE_REQUIRED_DRILLS[profile_record["name"]]
        latest_drills = self._latest_drills(
            actor, profile_record["name"]
        )
        checks = [
            {"name": "database_connection", "status": database_status},
            {
                "name": "operational_slo",
                "status": slo["status"],
                "coverage_complete": slo["coverage"]["complete"],
                "uptime_percent": slo["uptime_percent"],
            },
        ]
        checks.extend(
            self._drill_check(
                drill_type,
                latest_drills.get(drill_type),
                reference_time,
            )
            for drill_type in sorted(required_drills)
        )
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
                    "run_id": integration.get("run_id"),
                }
            )
        statuses = {check["status"] for check in checks}
        complete = bool(checks) and statuses == {"PASS"}
        status = (
            "PASS"
            if complete
            else "FAIL"
            if "FAIL" in statuses
            else "NOT_VERIFIED"
        )
        return {
            "profile": profile_record,
            "checks": checks,
            "required_drills": sorted(required_drills),
            "ready_for_deployment_claim": complete,
            "ready_for_public_claim": (
                complete if profile_record["name"] == "public" else False
            ),
            "status": status,
            "slo": slo,
            "policy": {
                "configuration_is_not_operational_proof": True,
                "current_drills_override_older_results": True,
                "external_security_review_is_required": (
                    "SECURITY_REVIEW" in required_drills
                ),
                "ai_api_required": False,
            },
        }
