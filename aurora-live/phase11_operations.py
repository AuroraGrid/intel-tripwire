from __future__ import annotations

import json
import secrets
from statistics import mean

from phase10_assets import all_static_assets
from phase10_complete import route_exposure
from storage import now, sid

OUTCOMES = {"true_positive", "false_positive", "false_negative"}


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def loads(value, default):
    try:
        return json.loads(value) if value else default
    except (TypeError, json.JSONDecodeError):
        return default


class DecisionOperations:
    def __init__(self, store, forecasts, outputs):
        self.store = store
        self.forecasts = forecasts
        self.outputs = outputs
        self.init()

    def init(self):
        with self.store.db() as connection:
            connection.executescript("""
            CREATE TABLE IF NOT EXISTS scenario_branches(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, forecast_id TEXT NOT NULL,
                parent_id TEXT, name TEXT NOT NULL, probability REAL NOT NULL, assumptions TEXT NOT NULL,
                dependencies TEXT NOT NULL, decision_costs TEXT NOT NULL, created_by TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS alert_outcomes(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, alert_id TEXT,
                outcome TEXT NOT NULL, detected_at TEXT, decided_at TEXT, recorded_by TEXT NOT NULL,
                metadata TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_scenarios_forecast ON scenario_branches(workspace_id,forecast_id,created_at);
            CREATE INDEX IF NOT EXISTS idx_alert_outcomes_workspace ON alert_outcomes(workspace_id,created_at);
            """)

    @staticmethod
    def _probability(value):
        value = float(value)
        if value < 0 or value > 1:
            raise ValueError("probability must be between 0 and 1")
        return value

    def create_scenario(self, actor, forecast_id, payload):
        self.forecasts.get(actor, forecast_id, include_history=False)
        name = str(payload.get("name", "")).strip()
        if not name:
            raise ValueError("name required")
        probability = self._probability(payload.get("probability"))
        parent_id = str(payload.get("parent_id", "")).strip() or None
        if parent_id:
            with self.store.db() as connection:
                parent = connection.execute(
                    "SELECT 1 FROM scenario_branches WHERE id=? AND forecast_id=? AND workspace_id=?",
                    (parent_id, forecast_id, actor["workspace_id"]),
                ).fetchone()
            if not parent:
                raise KeyError("parent scenario not found")
        stamp = now()
        scenario_id = sid("scenario", actor["workspace_id"], forecast_id, name, stamp, secrets.token_hex(4))
        assumptions = payload.get("assumptions") or []
        dependencies = payload.get("dependencies") or []
        costs = payload.get("decision_costs") or {}
        if not isinstance(assumptions, list) or not isinstance(dependencies, list) or not isinstance(costs, dict):
            raise ValueError("invalid scenario structure")
        with self.store.db() as connection:
            connection.execute(
                "INSERT INTO scenario_branches(id,workspace_id,forecast_id,parent_id,name,probability,assumptions,dependencies,decision_costs,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (scenario_id, actor["workspace_id"], forecast_id, parent_id, name, probability, dumps(assumptions), dumps(dependencies), dumps(costs), actor["id"], stamp),
            )
        self.store.identity.audit(actor["workspace_id"], actor["id"], "scenario.created", "scenario", scenario_id)
        return self.scenario_graph(actor, forecast_id)

    def scenario_graph(self, actor, forecast_id):
        self.forecasts.get(actor, forecast_id, include_history=False)
        with self.store.db() as connection:
            rows = connection.execute(
                "SELECT * FROM scenario_branches WHERE workspace_id=? AND forecast_id=? ORDER BY created_at",
                (actor["workspace_id"], forecast_id),
            ).fetchall()
        nodes = []
        edges = []
        for row in rows:
            item = dict(row)
            for field, default in (("assumptions", []), ("dependencies", []), ("decision_costs", {})):
                item[field] = loads(item[field], default)
            nodes.append(item)
            if item["parent_id"]:
                edges.append({"from": item["parent_id"], "to": item["id"], "type": "branches_to"})
            for dependency in item["dependencies"]:
                edges.append({"from": str(dependency), "to": item["id"], "type": "depends_on"})
        return {"forecast_id": forecast_id, "nodes": nodes, "edges": edges}

    def record_alert_outcome(self, actor, payload):
        outcome = str(payload.get("outcome", "")).strip().lower()
        if outcome not in OUTCOMES:
            raise ValueError("invalid outcome")
        stamp = now()
        record_id = sid("alert-outcome", actor["workspace_id"], payload.get("alert_id", ""), outcome, stamp, secrets.token_hex(4))
        metadata = payload.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise ValueError("metadata must be an object")
        with self.store.db() as connection:
            connection.execute(
                "INSERT INTO alert_outcomes(id,workspace_id,alert_id,outcome,detected_at,decided_at,recorded_by,metadata,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (record_id, actor["workspace_id"], str(payload.get("alert_id", "")).strip() or None, outcome, payload.get("detected_at"), payload.get("decided_at"), actor["id"], dumps(metadata), stamp),
            )
        return {"id": record_id, "outcome": outcome, "created_at": stamp}

    def performance(self, actor):
        with self.store.db() as connection:
            rows = [dict(row) for row in connection.execute(
                "SELECT * FROM alert_outcomes WHERE workspace_id=? ORDER BY created_at",
                (actor["workspace_id"],),
            ).fetchall()]
        counts = {name: sum(1 for row in rows if row["outcome"] == name) for name in OUTCOMES}
        tp, fp, fn = counts["true_positive"], counts["false_positive"], counts["false_negative"]
        precision = tp / (tp + fp) if tp + fp else None
        recall = tp / (tp + fn) if tp + fn else None
        latencies = []
        for row in rows:
            if row.get("detected_at") and row.get("decided_at"):
                try:
                    from datetime import datetime
                    start = datetime.fromisoformat(row["detected_at"].replace("Z", "+00:00"))
                    end = datetime.fromisoformat(row["decided_at"].replace("Z", "+00:00"))
                    latencies.append(max(0.0, (end - start).total_seconds()))
                except ValueError:
                    pass
        return {
            "records": len(rows), "counts": counts,
            "precision": None if precision is None else round(precision, 6),
            "recall": None if recall is None else round(recall, 6),
            "false_alarm_rate": None if tp + fp == 0 else round(fp / (tp + fp), 6),
            "mean_time_to_decision_seconds": None if not latencies else round(mean(latencies), 3),
        }

    def portfolio(self, actor):
        forecasts = self.forecasts.list(actor, limit=500)
        resolved = [item for item in forecasts if item["status"] == "resolved"]
        probabilities = [item["latest_revision"]["probability"] for item in forecasts if item.get("latest_revision")]
        modules = {}
        for output in self.outputs.list(actor, limit=500):
            modules[output["module"]] = modules.get(output["module"], 0) + 1
        return {
            "forecasts": forecasts,
            "summary": {
                "total": len(forecasts), "open": sum(item["status"] == "open" for item in forecasts),
                "resolved": len(resolved), "mean_probability": None if not probabilities else round(mean(probabilities), 6),
                "metrics": self.forecasts.metrics(actor), "module_outputs": modules,
            },
        }

    def hall_of_record(self, actor, limit=200):
        with self.store.db() as connection:
            forecasts = [dict(row) for row in connection.execute(
                "SELECT id,question,status,outcome,created_at,resolved_at FROM forecasts WHERE workspace_id=? ORDER BY created_at DESC LIMIT ?",
                (actor["workspace_id"], max(1, min(500, int(limit)))),
            ).fetchall()]
            outputs = [dict(row) for row in connection.execute(
                "SELECT id,module,subject_type,subject_id,status,summary,created_at FROM system_outputs WHERE workspace_id=? ORDER BY created_at DESC LIMIT ?",
                (actor["workspace_id"], max(1, min(500, int(limit)))),
            ).fetchall()]
            reviews = [dict(row) for row in connection.execute(
                "SELECT id,output_id,review_kind,decision,created_at FROM system_output_reviews WHERE workspace_id=? ORDER BY created_at DESC LIMIT ?",
                (actor["workspace_id"], max(1, min(500, int(limit)))),
            ).fetchall()]
        events = [dict(item, record_type="forecast") for item in forecasts]
        events += [dict(item, record_type="system_output") for item in outputs]
        events += [dict(item, record_type="review") for item in reviews]
        events.sort(key=lambda item: item.get("created_at") or "", reverse=True)
        return {"records": events[:limit], "count": min(limit, len(events))}

    def route_risk(self, actor, payload):
        points = payload.get("points") or []
        if not isinstance(points, list) or len(points) < 1:
            raise ValueError("points required")
        route = []
        for point in points:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                raise ValueError("each point must contain latitude and longitude")
            route.append((float(point[0]), float(point[1])))
        incidents = self.store.incidents(limit=500, workspace_id=actor["workspace_id"])
        result = route_exposure(route, all_static_assets(), incidents, float(payload.get("radius_degrees", 2)))
        result["decision_costs"] = payload.get("decision_costs") or {}
        return result
