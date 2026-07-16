from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone

from storage import sid

SIGNAL_TYPES = {"incident", "market", "weather", "aviation", "maritime", "infrastructure", "cyber", "satellite", "outage"}


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def loads(value, default):
    try:
        return json.loads(value) if value else default
    except (TypeError, json.JSONDecodeError):
        return default


class SignalFusion:
    def __init__(self, store):
        self.store = store
        self.init()

    def init(self):
        with self.store.db() as connection:
            connection.executescript("""
            CREATE TABLE IF NOT EXISTS live_signals(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, provider TEXT NOT NULL,
                signal_type TEXT NOT NULL, external_id TEXT, title TEXT NOT NULL,
                observed_at TEXT NOT NULL, latitude REAL, longitude REAL,
                confidence REAL NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL,
                UNIQUE(workspace_id,provider,signal_type,external_id)
            );
            CREATE TABLE IF NOT EXISTS provider_health(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, provider TEXT NOT NULL,
                checked_at TEXT NOT NULL, ok INTEGER NOT NULL, latency_ms REAL,
                records INTEGER NOT NULL, error TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS fused_events(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, title TEXT NOT NULL,
                category TEXT NOT NULL, confidence REAL NOT NULL, signal_ids TEXT NOT NULL,
                explanation TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_live_signals_workspace_type ON live_signals(workspace_id,signal_type,observed_at);
            CREATE INDEX IF NOT EXISTS idx_provider_health_workspace_provider ON provider_health(workspace_id,provider,checked_at);
            CREATE INDEX IF NOT EXISTS idx_fused_events_workspace ON fused_events(workspace_id,created_at);
            """)

    def ingest(self, actor, payload):
        provider = str(payload.get("provider", "")).strip().lower()
        signal_type = str(payload.get("signal_type", "")).strip().lower()
        title = str(payload.get("title", "")).strip()
        if not provider:
            raise ValueError("provider required")
        if signal_type not in SIGNAL_TYPES:
            raise ValueError("invalid signal_type")
        if not title:
            raise ValueError("title required")
        confidence = float(payload.get("confidence", 0.5))
        if not 0 <= confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        external_id = str(payload.get("external_id", "")).strip() or None
        signal_id = sid("live-signal", actor["workspace_id"], provider, signal_type, external_id or secrets.token_hex(6))
        stamp = now()
        values = (signal_id, actor["workspace_id"], provider, signal_type, external_id, title,
                  str(payload.get("observed_at") or stamp), payload.get("latitude"), payload.get("longitude"),
                  confidence, dumps(payload.get("payload") or {}), stamp)
        with self.store.db() as connection:
            connection.execute("""INSERT INTO live_signals(id,workspace_id,provider,signal_type,external_id,title,observed_at,latitude,longitude,confidence,payload,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(workspace_id,provider,signal_type,external_id) DO UPDATE SET
            title=excluded.title,observed_at=excluded.observed_at,latitude=excluded.latitude,longitude=excluded.longitude,
            confidence=excluded.confidence,payload=excluded.payload""", values)
        self.store.identity.audit(actor["workspace_id"], actor["id"], "signal.ingested", "live_signal", signal_id, metadata={"provider": provider, "signal_type": signal_type})
        return self.get(actor, signal_id)

    def get(self, actor, signal_id):
        with self.store.db() as connection:
            row = connection.execute("SELECT * FROM live_signals WHERE id=? AND workspace_id=?", (signal_id, actor["workspace_id"])).fetchone()
        if not row:
            raise KeyError("signal not found")
        item = dict(row)
        item["payload"] = loads(item["payload"], {})
        return item

    def list(self, actor, signal_type="", provider="", limit=200):
        sql = "SELECT id FROM live_signals WHERE workspace_id=?"
        args = [actor["workspace_id"]]
        if signal_type:
            if signal_type not in SIGNAL_TYPES:
                raise ValueError("invalid signal_type")
            sql += " AND signal_type=?"; args.append(signal_type)
        if provider:
            sql += " AND provider=?"; args.append(provider.lower())
        sql += " ORDER BY observed_at DESC LIMIT ?"; args.append(max(1, min(1000, int(limit))))
        with self.store.db() as connection:
            rows = connection.execute(sql, args).fetchall()
        return [self.get(actor, row["id"]) for row in rows]

    def record_health(self, actor, payload):
        provider = str(payload.get("provider", "")).strip().lower()
        if not provider:
            raise ValueError("provider required")
        checked = str(payload.get("checked_at") or now())
        health_id = sid("provider-health", actor["workspace_id"], provider, checked, secrets.token_hex(4))
        with self.store.db() as connection:
            connection.execute("INSERT INTO provider_health(id,workspace_id,provider,checked_at,ok,latency_ms,records,error) VALUES(?,?,?,?,?,?,?,?)",
                (health_id, actor["workspace_id"], provider, checked, 1 if payload.get("ok") else 0, payload.get("latency_ms"), int(payload.get("records", 0)), str(payload.get("error", ""))))
        return {"id": health_id, "provider": provider, "checked_at": checked}

    def provider_scorecard(self, actor):
        with self.store.db() as connection:
            rows = connection.execute("SELECT * FROM provider_health WHERE workspace_id=? ORDER BY checked_at", (actor["workspace_id"],)).fetchall()
        grouped = {}
        for row in rows:
            value = dict(row); grouped.setdefault(value["provider"], []).append(value)
        providers = []
        for name, values in grouped.items():
            total = len(values); ok = sum(int(v["ok"]) for v in values)
            latencies = [float(v["latency_ms"]) for v in values if v["latency_ms"] is not None]
            providers.append({"provider": name, "checks": total, "availability": ok / total if total else 0.0,
                              "mean_latency_ms": sum(latencies) / len(latencies) if latencies else None,
                              "records": sum(int(v["records"]) for v in values), "last_checked_at": values[-1]["checked_at"]})
        return {"providers": sorted(providers, key=lambda x: x["provider"])}

    def fuse(self, actor, payload):
        signal_ids = payload.get("signal_ids") or []
        if not isinstance(signal_ids, list) or len(signal_ids) < 2:
            raise ValueError("signal_ids must contain at least two signals")
        signals = [self.get(actor, value) for value in signal_ids]
        providers = {item["provider"] for item in signals}
        types = {item["signal_type"] for item in signals}
        confidence = min(1.0, sum(float(item["confidence"]) for item in signals) / len(signals) + min(0.2, 0.05 * (len(providers) - 1)))
        title = str(payload.get("title", "")).strip() or signals[0]["title"]
        category = str(payload.get("category", "cross_domain")).strip().lower()
        explanation = str(payload.get("explanation", "")).strip() or f"Correlated {len(signals)} signals across {len(providers)} providers and {len(types)} domains."
        event_id = sid("fused-event", actor["workspace_id"], *signal_ids, secrets.token_hex(4))
        with self.store.db() as connection:
            connection.execute("INSERT INTO fused_events(id,workspace_id,title,category,confidence,signal_ids,explanation,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (event_id, actor["workspace_id"], title, category, confidence, dumps(signal_ids), explanation, now()))
        return self.fused(actor, event_id)

    def fused(self, actor, event_id):
        with self.store.db() as connection:
            row = connection.execute("SELECT * FROM fused_events WHERE id=? AND workspace_id=?", (event_id, actor["workspace_id"])).fetchone()
        if not row:
            raise KeyError("fused event not found")
        item = dict(row); item["signal_ids"] = loads(item["signal_ids"], [])
        item["signals"] = [self.get(actor, signal_id) for signal_id in item["signal_ids"]]
        return item

    def fused_list(self, actor, limit=100):
        with self.store.db() as connection:
            rows = connection.execute("SELECT id FROM fused_events WHERE workspace_id=? ORDER BY created_at DESC LIMIT ?", (actor["workspace_id"], max(1, min(500, int(limit))))).fetchall()
        return [self.fused(actor, row["id"]) for row in rows]
