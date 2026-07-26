from __future__ import annotations

import json
import math
import re
from datetime import timedelta
from typing import Any

from phase26_operations import _format_timestamp, _parse_timestamp, _reject_future_timestamp
from storage import now, sid


POLICY_STATUSES = {"ACTIVE", "RETIRED"}
DEPLOYMENT_ENVIRONMENTS = {"PRODUCTION", "STAGING", "DEVELOPMENT", "DISASTER_RECOVERY"}
ATTESTATION_RESULTS = {"PASS", "FAIL", "NOT_VERIFIED"}
REFERENCE_KEYS = {"artifact", "checksum", "report", "report_url", "run_id", "sha256", "url"}
CONTROL_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,127}$")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _load(value: Any, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except (TypeError, json.JSONDecodeError):
        return default


def _required_text(value: Any, field: str, maximum: int = 200) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} required")
    if len(text) > maximum:
        raise ValueError(f"{field} exceeds {maximum} characters")
    return text


def _bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _evidence(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not any(str(value.get(key) or "").strip() for key in REFERENCE_KEYS):
        raise ValueError("evidence must contain a durable reference")
    return value


class EnterpriseControlPlane:
    """Workspace-scoped enterprise policy, deployment, and compliance records."""

    def __init__(self, store):
        self.store = store
        self.init()

    def init(self) -> None:
        with self.store.db() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS enterprise_policies(
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    policy_key TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    title TEXT NOT NULL,
                    controls TEXT NOT NULL,
                    allowed_regions TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    actor_user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(workspace_id,policy_key,version)
                );
                CREATE TABLE IF NOT EXISTS enterprise_deployments(
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    environment TEXT NOT NULL,
                    region TEXT NOT NULL,
                    data_residency TEXT NOT NULL,
                    encryption_at_rest INTEGER NOT NULL,
                    encryption_in_transit INTEGER NOT NULL,
                    audit_logging INTEGER NOT NULL,
                    external_ai_enabled INTEGER NOT NULL,
                    owner TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    actor_user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(workspace_id,name)
                );
                CREATE TABLE IF NOT EXISTS enterprise_attestations(
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    deployment_id TEXT NOT NULL,
                    control_key TEXT NOT NULL,
                    result TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    actor_user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_enterprise_policies
                    ON enterprise_policies(workspace_id,policy_key,version DESC);
                CREATE INDEX IF NOT EXISTS idx_enterprise_deployments
                    ON enterprise_deployments(workspace_id,environment,name);
                CREATE INDEX IF NOT EXISTS idx_enterprise_attestations
                    ON enterprise_attestations(workspace_id,deployment_id,control_key,observed_at DESC);
                """
            )

    @staticmethod
    def _workspace(actor: dict[str, Any]) -> str:
        return str(actor["workspace_id"])

    @staticmethod
    def _actor(actor: dict[str, Any]) -> str:
        return str(actor.get("id") or "system")

    def _admin(self, actor: dict[str, Any]) -> None:
        self.store.identity.require(actor, "admin")

    @staticmethod
    def _timestamp(value: Any, field: str = "observed_at") -> str:
        try:
            parsed = _parse_timestamp(value or now())
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} must be a timezone-aware ISO-8601 timestamp") from exc
        _reject_future_timestamp(parsed, field)
        return _format_timestamp(parsed)

    @staticmethod
    def _controls(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict) or not value:
            raise ValueError("controls must be a non-empty object")
        normalized: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key or "").strip().lower()
            if not CONTROL_KEY_RE.fullmatch(key):
                raise ValueError("control keys must use lowercase letters, numbers, dot, dash, or underscore")
            if isinstance(raw_value, bool):
                normalized[key] = raw_value
            elif isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
                number = float(raw_value)
                if not math.isfinite(number):
                    raise ValueError(f"control {key} must be finite")
                normalized[key] = number
            elif isinstance(raw_value, str) and raw_value.strip():
                normalized[key] = raw_value.strip()
            else:
                raise ValueError(f"unsupported value for control {key}")
        return normalized

    def publish_policy(self, actor: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        self._admin(actor)
        workspace = self._workspace(actor)
        policy_key = _required_text(payload.get("policy_key"), "policy_key", 128).lower()
        if not CONTROL_KEY_RE.fullmatch(policy_key):
            raise ValueError("policy_key has invalid characters")
        title = _required_text(payload.get("title"), "title")
        status = str(payload.get("status") or "ACTIVE").strip().upper()
        if status not in POLICY_STATUSES:
            raise ValueError("invalid policy status")
        controls = self._controls(payload.get("controls"))
        regions = sorted({_required_text(item, "allowed_regions item", 64).upper() for item in (payload.get("allowed_regions") or [])})
        metadata = payload.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise ValueError("metadata must be an object")
        with self.store.db() as connection:
            row = connection.execute(
                "SELECT MAX(version) AS version FROM enterprise_policies WHERE workspace_id=? AND policy_key=?",
                (workspace, policy_key),
            ).fetchone()
            version = int(row["version"] or 0) + 1
            policy_id = sid("enterprise-policy", workspace, policy_key, version)
            stamp = now()
            connection.execute(
                """
                INSERT INTO enterprise_policies(
                    id,workspace_id,policy_key,version,status,title,controls,
                    allowed_regions,metadata,actor_user_id,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    policy_id, workspace, policy_key, version, status, title,
                    _json(controls), _json(regions), _json(metadata), self._actor(actor), stamp,
                ),
            )
        self.store.identity.audit(
            workspace, self._actor(actor), "enterprise.policy.published",
            "enterprise_policy", policy_id,
            metadata={"policy_key": policy_key, "version": version, "status": status},
        )
        return self.policy(actor, policy_id)

    def policy(self, actor: dict[str, Any], policy_id: str) -> dict[str, Any]:
        with self.store.db() as connection:
            row = connection.execute(
                "SELECT * FROM enterprise_policies WHERE id=? AND workspace_id=?",
                (policy_id, self._workspace(actor)),
            ).fetchone()
        if not row:
            raise KeyError("enterprise policy not found")
        item = dict(row)
        item["controls"] = _load(item["controls"], {})
        item["allowed_regions"] = _load(item["allowed_regions"], [])
        item["metadata"] = _load(item["metadata"], {})
        return item

    def policies(self, actor: dict[str, Any], policy_key: str = "", limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT id FROM enterprise_policies WHERE workspace_id=?"
        args: list[Any] = [self._workspace(actor)]
        if policy_key:
            sql += " AND policy_key=?"
            args.append(policy_key.strip().lower())
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(max(1, min(500, int(limit))))
        with self.store.db() as connection:
            rows = connection.execute(sql, args).fetchall()
        return [self.policy(actor, row["id"]) for row in rows]

    def register_deployment(self, actor: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        self._admin(actor)
        workspace = self._workspace(actor)
        name = _required_text(payload.get("name"), "name")
        environment = str(payload.get("environment") or "PRODUCTION").strip().upper()
        if environment not in DEPLOYMENT_ENVIRONMENTS:
            raise ValueError("invalid deployment environment")
        region = _required_text(payload.get("region"), "region", 64).upper()
        data_residency = _required_text(payload.get("data_residency") or region, "data_residency", 64).upper()
        owner = _required_text(payload.get("owner"), "owner")
        fields = {
            "encryption_at_rest": _bool(payload.get("encryption_at_rest"), "encryption_at_rest"),
            "encryption_in_transit": _bool(payload.get("encryption_in_transit"), "encryption_in_transit"),
            "audit_logging": _bool(payload.get("audit_logging"), "audit_logging"),
            "external_ai_enabled": _bool(payload.get("external_ai_enabled", False), "external_ai_enabled"),
        }
        metadata = payload.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise ValueError("metadata must be an object")
        deployment_id = sid("enterprise-deployment", workspace, name.casefold())
        stamp = now()
        with self.store.db() as connection:
            existing = connection.execute(
                "SELECT created_at FROM enterprise_deployments WHERE id=? AND workspace_id=?",
                (deployment_id, workspace),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO enterprise_deployments(
                    id,workspace_id,name,environment,region,data_residency,
                    encryption_at_rest,encryption_in_transit,audit_logging,
                    external_ai_enabled,owner,metadata,actor_user_id,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(workspace_id,name) DO UPDATE SET
                    environment=excluded.environment,region=excluded.region,
                    data_residency=excluded.data_residency,
                    encryption_at_rest=excluded.encryption_at_rest,
                    encryption_in_transit=excluded.encryption_in_transit,
                    audit_logging=excluded.audit_logging,
                    external_ai_enabled=excluded.external_ai_enabled,
                    owner=excluded.owner,metadata=excluded.metadata,
                    actor_user_id=excluded.actor_user_id,updated_at=excluded.updated_at
                """,
                (
                    deployment_id, workspace, name, environment, region, data_residency,
                    int(fields["encryption_at_rest"]), int(fields["encryption_in_transit"]),
                    int(fields["audit_logging"]), int(fields["external_ai_enabled"]),
                    owner, _json(metadata), self._actor(actor),
                    existing["created_at"] if existing else stamp, stamp,
                ),
            )
        self.store.identity.audit(
            workspace, self._actor(actor), "enterprise.deployment.registered",
            "enterprise_deployment", deployment_id,
            metadata={"name": name, "environment": environment, "region": region},
        )
        return self.deployment(actor, deployment_id)

    def deployment(self, actor: dict[str, Any], deployment_id: str) -> dict[str, Any]:
        with self.store.db() as connection:
            row = connection.execute(
                "SELECT * FROM enterprise_deployments WHERE id=? AND workspace_id=?",
                (deployment_id, self._workspace(actor)),
            ).fetchone()
        if not row:
            raise KeyError("enterprise deployment not found")
        item = dict(row)
        for key in ("encryption_at_rest", "encryption_in_transit", "audit_logging", "external_ai_enabled"):
            item[key] = bool(item[key])
        item["metadata"] = _load(item["metadata"], {})
        return item

    def deployments(self, actor: dict[str, Any], environment: str = "", limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT id FROM enterprise_deployments WHERE workspace_id=?"
        args: list[Any] = [self._workspace(actor)]
        if environment:
            sql += " AND environment=?"
            args.append(environment.strip().upper())
        sql += " ORDER BY updated_at DESC LIMIT ?"
        args.append(max(1, min(500, int(limit))))
        with self.store.db() as connection:
            rows = connection.execute(sql, args).fetchall()
        return [self.deployment(actor, row["id"]) for row in rows]

    def record_attestation(self, actor: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        self._admin(actor)
        deployment_id = _required_text(payload.get("deployment_id"), "deployment_id", 128)
        self.deployment(actor, deployment_id)
        control_key = _required_text(payload.get("control_key"), "control_key", 128).lower()
        if not CONTROL_KEY_RE.fullmatch(control_key):
            raise ValueError("control_key has invalid characters")
        result = str(payload.get("result") or "NOT_VERIFIED").strip().upper()
        if result not in ATTESTATION_RESULTS:
            raise ValueError("invalid attestation result")
        evidence = _evidence(payload.get("evidence"))
        observed_at = self._timestamp(payload.get("observed_at"))
        try:
            days = int(payload.get("valid_days", 30))
        except (TypeError, ValueError) as exc:
            raise ValueError("valid_days must be an integer") from exc
        if not 1 <= days <= 365:
            raise ValueError("valid_days must be between 1 and 365")
        observed_dt = _parse_timestamp(observed_at)
        expires_at = _format_timestamp(observed_dt + timedelta(days=days))
        workspace = self._workspace(actor)
        attestation_id = sid("enterprise-attestation", workspace, deployment_id, control_key, observed_at, _json(evidence))
        with self.store.db() as connection:
            existing = connection.execute(
                "SELECT id FROM enterprise_attestations WHERE id=? AND workspace_id=?",
                (attestation_id, workspace),
            ).fetchone()
            if not existing:
                connection.execute(
                    """
                    INSERT INTO enterprise_attestations(
                        id,workspace_id,deployment_id,control_key,result,evidence,
                        observed_at,expires_at,actor_user_id,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        attestation_id, workspace, deployment_id, control_key, result,
                        _json(evidence), observed_at, expires_at, self._actor(actor), now(),
                    ),
                )
        self.store.identity.audit(
            workspace, self._actor(actor), "enterprise.attestation.recorded",
            "enterprise_attestation", attestation_id,
            metadata={"deployment_id": deployment_id, "control_key": control_key, "result": result, "duplicate": bool(existing)},
        )
        item = self.attestation(actor, attestation_id)
        item["duplicate"] = bool(existing)
        return item

    def attestation(self, actor: dict[str, Any], attestation_id: str) -> dict[str, Any]:
        with self.store.db() as connection:
            row = connection.execute(
                "SELECT * FROM enterprise_attestations WHERE id=? AND workspace_id=?",
                (attestation_id, self._workspace(actor)),
            ).fetchone()
        if not row:
            raise KeyError("enterprise attestation not found")
        item = dict(row)
        item["evidence"] = _load(item["evidence"], {})
        return item

    def attestations(self, actor: dict[str, Any], deployment_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT id FROM enterprise_attestations WHERE workspace_id=?"
        args: list[Any] = [self._workspace(actor)]
        if deployment_id:
            sql += " AND deployment_id=?"
            args.append(deployment_id)
        sql += " ORDER BY observed_at DESC,created_at DESC LIMIT ?"
        args.append(max(1, min(500, int(limit))))
        with self.store.db() as connection:
            rows = connection.execute(sql, args).fetchall()
        return [self.attestation(actor, row["id"]) for row in rows]

    def _active_policies(self, actor: dict[str, Any]) -> list[dict[str, Any]]:
        with self.store.db() as connection:
            rows = connection.execute(
                """
                SELECT p.id FROM enterprise_policies p
                JOIN (
                    SELECT policy_key,MAX(version) AS version
                    FROM enterprise_policies
                    WHERE workspace_id=? GROUP BY policy_key
                ) latest ON latest.policy_key=p.policy_key AND latest.version=p.version
                WHERE p.workspace_id=? AND p.status='ACTIVE'
                ORDER BY p.policy_key
                """,
                (self._workspace(actor), self._workspace(actor)),
            ).fetchall()
        return [self.policy(actor, row["id"]) for row in rows]

    def compliance(self, actor: dict[str, Any], deployment_id: str = "") -> dict[str, Any]:
        deployments = [self.deployment(actor, deployment_id)] if deployment_id else self.deployments(actor, limit=500)
        policies = self._active_policies(actor)
        workspace = self._workspace(actor)
        stamp = _parse_timestamp(now())
        output = []
        for deployment in deployments:
            checks = []
            for policy in policies:
                if policy["allowed_regions"] and deployment["data_residency"] not in policy["allowed_regions"]:
                    checks.append({"policy_key": policy["policy_key"], "control_key": "data_residency", "expected": policy["allowed_regions"], "actual": deployment["data_residency"], "status": "FAIL"})
                for control_key, expected in policy["controls"].items():
                    if control_key in deployment:
                        actual = deployment[control_key]
                        status = "PASS" if actual == expected else "FAIL"
                    else:
                        with self.store.db() as connection:
                            row = connection.execute(
                                """
                                SELECT result,expires_at FROM enterprise_attestations
                                WHERE workspace_id=? AND deployment_id=? AND control_key=?
                                ORDER BY observed_at DESC,created_at DESC LIMIT 1
                                """,
                                (workspace, deployment["id"], control_key),
                            ).fetchone()
                        if not row or _parse_timestamp(row["expires_at"]) < stamp:
                            actual, status = None, "NOT_VERIFIED"
                        else:
                            actual, status = row["result"], row["result"]
                    checks.append({"policy_key": policy["policy_key"], "control_key": control_key, "expected": expected, "actual": actual, "status": status})
            counts = {name: sum(item["status"] == name for item in checks) for name in ("PASS", "FAIL", "NOT_VERIFIED")}
            overall = "FAIL" if counts["FAIL"] else ("NOT_VERIFIED" if counts["NOT_VERIFIED"] or not checks else "PASS")
            output.append({"deployment": deployment, "checks": checks, "counts": counts, "status": overall})
        totals = {name: sum(item["status"] == name for item in output) for name in ("PASS", "FAIL", "NOT_VERIFIED")}
        return {
            "phase": 29,
            "policies": policies,
            "deployments": output,
            "counts": totals,
            "enterprise_ready": bool(output) and totals["FAIL"] == 0 and totals["NOT_VERIFIED"] == 0,
            "policy": {"latest_policy_version_controls": True, "expired_attestations_do_not_pass": True, "external_ai_required": False},
        }
