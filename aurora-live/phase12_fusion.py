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
        observed_at = str(payload.get("observed_at") or now())
        external_id = str(payload.get("external_id", "")).strip() or None
        signal_id = sid("live-signal", actor["workspace_id"], provider, signal_type, external_id or secrets.token_hex(6))
        with self.store.db() as connection:
            connection.execute(
                """INSERT INTO live_signals(id,workspace_id,provider,signal_type,external_id,title,observed_at,latitude,longitude,confidence,payload,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(workspace_id,provider,signal_type,external_id) DO UPDATE SET
                title=excluded.title,observed_at=excluded.observed_at,latitude=excluded.latitude,longitude=excluded.longitude,
                confidence=excluded.confidence,payload=excluded