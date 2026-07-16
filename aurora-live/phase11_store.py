from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone

from phase11_metrics import scorecard, validate_probability
from storage import sid


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def loads(value, default):
    try:
        return json.loads(value) if value else default
    except (TypeError, json.JSONDecodeError):
        return default


class ForecastLedger:
    def __init__(self, store):
        self.store = store
        self.init()

    def init(self):
        with self.store.db() as connection:
            connection.executescript("""
            CREATE TABLE IF NOT EXISTS forecasts(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, question TEXT NOT NULL,
                description TEXT, horizon TEXT, status TEXT NOT NULL, created_by TEXT NOT NULL,
                created_at TEXT NOT NULL, resolved_at TEXT, outcome INTEGER, resolution_note TEXT
            );
            CREATE TABLE IF NOT EXISTS forecast_revisions(
                id TEXT PRIMARY KEY, forecast_id TEXT NOT NULL REFERENCES forecasts(id) ON DELETE CASCADE,
                workspace_id TEXT NOT NULL, actor_user_id TEXT NOT NULL, probability REAL NOT NULL,
                confidence_low REAL, confidence_high REAL, source TEXT NOT NULL, rationale TEXT,
                trigger_map TEXT, falsifiers TEXT, evidence_links TEXT, scenarios TEXT,
                decision_costs TEXT, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS forecast_resolutions(
                id TEXT PRIMARY KEY, forecast_id TEXT NOT NULL REFERENCES forecasts(id) ON DELETE CASCADE,
                workspace_id TEXT NOT NULL, actor_user_id TEXT NOT NULL, outcome INTEGER NOT NULL,
                note TEXT, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_forecasts_workspace_status ON forecasts(workspace_id,status,created_at);
            CREATE INDEX IF NOT EXISTS idx_forecast_revisions_forecast ON forecast_revisions(forecast_id,created_at);
            """)

    def create(self, actor, payload):
        question = str(payload.get("question", "")).strip()
        if not question:
            raise ValueError("question required")
        workspace_id = actor["workspace_id"]
        stamp = now()
        forecast_id = sid("forecast", workspace_id, question, stamp, secrets.token_hex(4))
        with self.store.db() as connection:
            connection.execute(
                "INSERT INTO forecasts(id,workspace_id,question,description,horizon,status,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (forecast_id, workspace_id, question, str(payload.get("description", "")).strip(), str(payload.get("horizon", "")).strip(), "open", actor["id"], stamp),
            )
        self.store.identity.audit(workspace_id, actor["id"], "forecast.created", "forecast", forecast_id)
        probability = payload.get("probability")
        if probability is not None:
            self.revise(actor, forecast_id, payload)
        return self.get(actor, forecast_id)

    def revise(self, actor, forecast_id, payload):
        forecast = self.get(actor, forecast_id, include_history=False)
        if forecast["status"] != "open":
            raise ValueError("forecast is resolved")
        probability = validate_probability(payload.get("probability"))
        low = payload.get("confidence_low")
        high = payload.get("confidence_high")
        low = probability if low is None else validate_probability(low)
        high = probability if high is None else validate_probability(high)
        if low > probability or high < probability or low > high:
            raise ValueError("confidence interval must contain probability")
        stamp = now()
        revision_id = sid("forecast-revision", forecast_id, actor["id"], stamp, secrets.token_hex(4))
        with self.store.db() as connection:
            connection.execute(
                """INSERT INTO forecast_revisions(id,forecast_id,workspace_id,actor_user_id,probability,confidence_low,confidence_high,source,rationale,trigger_map,falsifiers,evidence_links,scenarios,decision_costs,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (revision_id, forecast_id, actor["workspace_id"], actor["id"], probability, low, high, str(payload.get("source", "analyst")), str(payload.get("rationale", "")), dumps(payload.get("trigger_map") or []), dumps(payload.get("falsifiers") or []), dumps(payload.get("evidence_links") or []), dumps(payload.get("scenarios") or []), dumps(payload.get("decision_costs") or {}), stamp),
            )
        self.store.identity.audit(actor["workspace_id"], actor["id"], "forecast.revised", "forecast", forecast_id, metadata={"revision_id": revision_id, "probability": probability})
        return self.get(actor, forecast_id)

    def resolve(self, actor, forecast_id, outcome, note=""):
        forecast = self.get(actor, forecast_id, include_history=False)
        if forecast["status"] != "open":
            raise ValueError("forecast already resolved")
        observed = 1 if bool(outcome) else 0
        stamp = now()
        resolution_id = sid("forecast-resolution", forecast_id, actor["id"], stamp, secrets.token_hex(4))
        with self.store.db() as connection:
            connection.execute("UPDATE forecasts SET status='resolved',resolved_at=?,outcome=?,resolution_note=? WHERE id=? AND workspace_id=?", (stamp, observed, str(note), forecast_id, actor["workspace_id"]))
            connection.execute("INSERT INTO forecast_resolutions(id,forecast_id,workspace_id,actor_user_id,outcome,note,created_at) VALUES(?,?,?,?,?,?,?)", (resolution_id, forecast_id, actor["workspace_id"], actor["id"], observed, str(note), stamp))
        self.store.identity.audit(actor["workspace_id"], actor["id"], "forecast.resolved", "forecast", forecast_id, metadata={"outcome": observed})
        return self.get(actor, forecast_id)

    def list(self, actor, status="", limit=100):
        sql = "SELECT id FROM forecasts WHERE workspace_id=?"
        args = [actor["workspace_id"]]
        if status:
            sql += " AND status=?"
            args.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(max(1, min(500, int(limit))))
        with self.store.db() as connection:
            rows = connection.execute(sql, args).fetchall()
        return [self.get(actor, row["id"], include_history=False) for row in rows]

    def get(self, actor, forecast_id, include_history=True):
        with self.store.db() as connection:
            row = connection.execute("SELECT * FROM forecasts WHERE id=? AND workspace_id=?", (forecast_id, actor["workspace_id"])).fetchone()
        if not row:
            raise KeyError("forecast not found")
        item = dict(row)
        item["outcome"] = None if item["outcome"] is None else bool(item["outcome"])
        with self.store.db() as connection:
            revisions = connection.execute("SELECT * FROM forecast_revisions WHERE forecast_id=? AND workspace_id=? ORDER BY created_at", (forecast_id, actor["workspace_id"])).fetchall()
        history = []
        for revision in revisions:
            value = dict(revision)
            for field, default in (("trigger_map", []), ("falsifiers", []), ("evidence_links", []), ("scenarios", []), ("decision_costs", {})):
                value[field] = loads(value[field], default)
            history.append(value)
        item["latest_revision"] = history[-1] if history else None
        if include_history:
            item["revisions"] = history
        return item

    def metrics(self, actor, source=""):
        sql = """SELECT r.probability,f.outcome,r.source FROM forecast_revisions r JOIN forecasts f ON f.id=r.forecast_id
        WHERE r.workspace_id=? AND f.status='resolved' AND r.created_at=(SELECT MAX(r2.created_at) FROM forecast_revisions r2 WHERE r2.forecast_id=r.forecast_id)"""
        args = [actor["workspace_id"]]
        if source:
            sql += " AND r.source=?"
            args.append(source)
        with self.store.db() as connection:
            rows = [dict(row) for row in connection.execute(sql, args).fetchall()]
        return scorecard(rows)
