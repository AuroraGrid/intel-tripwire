from __future__ import annotations

import json
import math
from typing import Any

from storage import now, sid

RESULTS = {"AHEAD", "PARITY", "BEHIND", "NOT_VERIFIED"}
EVIDENCE_KEYS = {"artifact", "benchmark", "report", "run_id", "sha256", "url"}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _load(value: Any, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except (TypeError, json.JSONDecodeError):
        return default


def _text(value: Any, field: str, maximum: int = 300) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} required")
    if len(text) > maximum:
        raise ValueError(f"{field} exceeds {maximum} characters")
    return text


def _number(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


def _evidence(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not any(str(value.get(key) or "").strip() for key in EVIDENCE_KEYS):
        raise ValueError("external evidence must contain a durable reference")
    return value


class ContinuousBenchmark:
    """Immutable, workspace-scoped competitive benchmark and regression ledger."""

    def __init__(self, store):
        self.store = store
        self.init()

    def init(self) -> None:
        with self.store.db() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS benchmark_targets(
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    target_key TEXT NOT NULL,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    active INTEGER NOT NULL,
                    metadata TEXT NOT NULL,
                    actor_user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(workspace_id,target_key)
                );
                CREATE TABLE IF NOT EXISTS benchmark_runs(
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    run_key TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    actor_user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(workspace_id,run_key)
                );
                CREATE TABLE IF NOT EXISTS benchmark_observations(
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    metric_key TEXT NOT NULL,
                    aurora_value REAL NOT NULL,
                    competitor_value REAL NOT NULL,
                    tolerance REAL NOT NULL,
                    direction TEXT NOT NULL,
                    result TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(workspace_id,run_id,metric_key)
                );
                CREATE TABLE IF NOT EXISTS benchmark_alerts(
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    metric_key TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(workspace_id,run_id,metric_key,reason)
                );
                CREATE INDEX IF NOT EXISTS idx_benchmark_runs
                    ON benchmark_runs(workspace_id,target_id,created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_benchmark_observations
                    ON benchmark_observations(workspace_id,metric_key,created_at DESC);
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

    def upsert_target(self, actor: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        self._admin(actor)
        workspace = self._workspace(actor)
        key = _text(payload.get("target_key"), "target_key", 128).lower()
        name = _text(payload.get("name"), "name")
        category = _text(payload.get("category"), "category", 100)
        active = int(bool(payload.get("active", True)))
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        stamp = now()
        target_id = sid("benchmark-target", workspace, key)
        with self.store.db() as connection:
            connection.execute(
                """INSERT INTO benchmark_targets(id,workspace_id,target_key,name,category,active,metadata,actor_user_id,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(workspace_id,target_key) DO UPDATE SET
                name=excluded.name,category=excluded.category,active=excluded.active,metadata=excluded.metadata,
                actor_user_id=excluded.actor_user_id,updated_at=excluded.updated_at""",
                (target_id, workspace, key, name, category, active, _json(metadata), self._actor(actor), stamp, stamp),
            )
        self.store.identity.audit(workspace, self._actor(actor), "benchmark.target.upserted", "benchmark_target", target_id)
        return self.target(actor, target_id)

    def target(self, actor: dict[str, Any], target_id: str) -> dict[str, Any]:
        with self.store.db() as connection:
            row = connection.execute(
                "SELECT * FROM benchmark_targets WHERE workspace_id=? AND id=?",
                (self._workspace(actor), target_id),
            ).fetchone()
        if not row:
            raise KeyError("benchmark target not found")
        item = dict(row)
        item["metadata"] = _load(item["metadata"], {})
        item["active"] = bool(item["active"])
        return item

    def targets(self, actor: dict[str, Any], limit: int = 100) -> list[dict[str, Any]]:
        with self.store.db() as connection:
            rows = connection.execute(
                "SELECT id FROM benchmark_targets WHERE workspace_id=? ORDER BY target_key LIMIT ?",
                (self._workspace(actor), max(1, min(500, int(limit)))),
            ).fetchall()
        return [self.target(actor, row["id"]) for row in rows]

    @staticmethod
    def _result(aurora: float, competitor: float, tolerance: float, direction: str) -> str:
        if direction == "higher":
            delta = aurora - competitor
        elif direction == "lower":
            delta = competitor - aurora
        else:
            raise ValueError("direction must be higher or lower")
        if abs(delta) <= tolerance:
            return "PARITY"
        return "AHEAD" if delta > tolerance else "BEHIND"

    def create_run(self, actor: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        self._admin(actor)
        workspace = self._workspace(actor)
        run_key = _text(payload.get("run_key"), "run_key", 128)
        target = self.target(actor, _text(payload.get("target_id"), "target_id", 100))
        observations = payload.get("observations")
        if not isinstance(observations, list) or not observations:
            raise ValueError("observations required")
        stamp = now()
        run_id = sid("benchmark-run", workspace, run_key)
        prepared = []
        for raw in observations:
            if not isinstance(raw, dict):
                raise ValueError("each observation must be an object")
            metric = _text(raw.get("metric_key"), "metric_key", 128).lower()
            aurora = _number(raw.get("aurora_value"), "aurora_value")
            competitor = _number(raw.get("competitor_value"), "competitor_value")
            tolerance = max(0.0, _number(raw.get("tolerance", 0), "tolerance"))
            direction = str(raw.get("direction") or "higher").lower()
            evidence = _evidence(raw.get("evidence"))
            result = self._result(aurora, competitor, tolerance, direction)
            prepared.append((metric, aurora, competitor, tolerance, direction, result, evidence))
        summary = {
            "target": target["target_key"],
            "ahead": sum(row[5] == "AHEAD" for row in prepared),
            "parity": sum(row[5] == "PARITY" for row in prepared),
            "behind": sum(row[5] == "BEHIND" for row in prepared),
            "not_verified": 0,
            "superiority_claim_allowed": bool(prepared) and all(row[5] in {"AHEAD", "PARITY"} for row in prepared),
        }
        with self.store.db() as connection:
            existing = connection.execute(
                "SELECT id FROM benchmark_runs WHERE workspace_id=? AND run_key=?",
                (workspace, run_key),
            ).fetchone()
            if existing:
                return self.run(actor, existing["id"])
            connection.execute(
                "INSERT INTO benchmark_runs(id,workspace_id,run_key,target_id,status,summary,actor_user_id,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (run_id, workspace, run_key, target["id"], "COMPLETE", _json(summary), self._actor(actor), stamp),
            )
            for metric, aurora, competitor, tolerance, direction, result, evidence in prepared:
                connection.execute(
                    """INSERT INTO benchmark_observations(id,workspace_id,run_id,metric_key,aurora_value,competitor_value,tolerance,direction,result,evidence,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (sid("benchmark-observation", workspace, run_id, metric), workspace, run_id, metric, aurora, competitor, tolerance, direction, result, _json(evidence), stamp),
                )
                if result == "BEHIND":
                    connection.execute(
                        "INSERT INTO benchmark_alerts(id,workspace_id,run_id,metric_key,severity,reason,created_at) VALUES(?,?,?,?,?,?,?)",
                        (sid("benchmark-alert", workspace, run_id, metric, "regression"), workspace, run_id, metric, "HIGH", "competitive regression or deficit", stamp),
                    )
        self.store.identity.audit(workspace, self._actor(actor), "benchmark.run.completed", "benchmark_run", run_id, metadata=summary)
        return self.run(actor, run_id)

    def run(self, actor: dict[str, Any], run_id: str) -> dict[str, Any]:
        workspace = self._workspace(actor)
        with self.store.db() as connection:
            row = connection.execute(
                "SELECT * FROM benchmark_runs WHERE workspace_id=? AND id=?",
                (workspace, run_id),
            ).fetchone()
            observations = connection.execute(
                "SELECT * FROM benchmark_observations WHERE workspace_id=? AND run_id=? ORDER BY metric_key",
                (workspace, run_id),
            ).fetchall()
            alerts = connection.execute(
                "SELECT * FROM benchmark_alerts WHERE workspace_id=? AND run_id=? ORDER BY metric_key",
                (workspace, run_id),
            ).fetchall()
        if not row:
            raise KeyError("benchmark run not found")
        item = dict(row)
        item["summary"] = _load(item["summary"], {})
        item["observations"] = []
        for observation in observations:
            value = dict(observation)
            value["evidence"] = _load(value["evidence"], {})
            item["observations"].append(value)
        item["alerts"] = [dict(alert) for alert in alerts]
        return item

    def runs(self, actor: dict[str, Any], target_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
        workspace = self._workspace(actor)
        sql = "SELECT id FROM benchmark_runs WHERE workspace_id=?"
        args: list[Any] = [workspace]
        if target_id:
            sql += " AND target_id=?"
            args.append(target_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(max(1, min(500, int(limit))))
        with self.store.db() as connection:
            rows = connection.execute(sql, args).fetchall()
        return [self.run(actor, row["id"]) for row in rows]

    def latest(self, actor: dict[str, Any], target_id: str) -> dict[str, Any]:
        with self.store.db() as connection:
            row = connection.execute(
                "SELECT id FROM benchmark_runs WHERE workspace_id=? AND target_id=? ORDER BY created_at DESC LIMIT 1",
                (self._workspace(actor), target_id),
            ).fetchone()
        if not row:
            return {"status": "NOT_RUN", "target_id": target_id, "superiority_claim_allowed": False}
        return self.run(actor, row["id"])
