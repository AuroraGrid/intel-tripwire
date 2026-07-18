from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def stable_id(*values: Any) -> str:
    return hashlib.sha256("|".join(map(str, values)).encode()).hexdigest()[:24]


DEFAULT_SENSORS = [
    {"id": "news.gdelt", "domain": "news", "provider": "GDELT", "transport": "https", "authority": "secondary", "enabled": True},
    {"id": "seismic.usgs", "domain": "seismic", "provider": "USGS", "transport": "https", "authority": "primary", "enabled": True},
    {"id": "disaster.gdacs", "domain": "disaster", "provider": "GDACS", "transport": "rss", "authority": "primary", "enabled": True},
    {"id": "natural.nasa_eonet", "domain": "natural_events", "provider": "NASA EONET", "transport": "https", "authority": "primary", "enabled": True},
    {"id": "wildfire.nasa_firms", "domain": "wildfire", "provider": "NASA FIRMS", "transport": "https", "authority": "primary", "enabled": False},
    {"id": "aviation.opensky", "domain": "aviation", "provider": "OpenSky", "transport": "https", "authority": "sensor_network", "enabled": False},
    {"id": "maritime.ais", "domain": "maritime", "provider": "AIS provider", "transport": "stream", "authority": "sensor_network", "enabled": False},
    {"id": "internet.bgp", "domain": "internet", "provider": "BGP telemetry", "transport": "stream", "authority": "sensor_network", "enabled": False},
    {"id": "weather.noaa", "domain": "weather", "provider": "NOAA", "transport": "https", "authority": "primary", "enabled": False},
    {"id": "sanctions.ofac", "domain": "sanctions", "provider": "OFAC", "transport": "https", "authority": "primary", "enabled": False},
    {"id": "markets.multi", "domain": "markets", "provider": "Market data provider", "transport": "https", "authority": "market", "enabled": False},
    {"id": "cyber.cisa", "domain": "cyber", "provider": "CISA", "transport": "https", "authority": "primary", "enabled": False},
]


class SensorMesh:
    """Persistent registry, health ledger, normalization gate, and coverage scorecard."""

    VALID_STATUSES = {"live", "degraded", "offline", "unknown"}

    def __init__(self, store):
        self.store = store
        self._init_schema()
        self._migrate_schema()
        self._ensure_seed(self._workspace())

    def _init_schema(self) -> None:
        with self.store.db() as connection:
            connection.executescript("""
            CREATE TABLE IF NOT EXISTS sensor_registry(
                id TEXT PRIMARY KEY, workspace_id TEXT, logical_id TEXT, domain TEXT, provider TEXT,
                transport TEXT, authority TEXT, enabled INTEGER, configuration TEXT,
                created_at TEXT, updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS sensor_health(
                id TEXT PRIMARY KEY, workspace_id TEXT, sensor_id TEXT, status TEXT,
                latency_ms INTEGER, records_seen INTEGER, error_rate REAL,
                last_success_at TEXT, last_error TEXT, observed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS sensor_observations(
                id TEXT PRIMARY KEY, workspace_id TEXT, sensor_id TEXT, domain TEXT,
                external_id TEXT, observed_at TEXT, latitude REAL, longitude REAL,
                severity TEXT, title TEXT, payload TEXT, dedupe_key TEXT,
                UNIQUE(workspace_id,sensor_id,dedupe_key)
            );
            CREATE INDEX IF NOT EXISTS idx_sensor_registry_workspace ON sensor_registry(workspace_id,domain,enabled);
            CREATE INDEX IF NOT EXISTS idx_sensor_health_workspace ON sensor_health(workspace_id,sensor_id,observed_at);
            CREATE INDEX IF NOT EXISTS idx_sensor_observations_workspace ON sensor_observations(workspace_id,domain,observed_at);
            """)

    def _migrate_schema(self) -> None:
        columns = set(self.store.database.column_names("sensor_registry"))
        with self.store.db() as connection:
            if "logical_id" not in columns:
                connection.execute("ALTER TABLE sensor_registry ADD COLUMN logical_id TEXT")
            connection.execute("UPDATE sensor_registry SET logical_id=id WHERE logical_id IS NULL OR logical_id='' ")
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_sensor_registry_identity ON sensor_registry(workspace_id,logical_id)")

    def _workspace(self, actor=None) -> str:
        if isinstance(actor, dict) and actor.get("workspace_id"):
            return str(actor["workspace_id"])
        if isinstance(actor, str) and actor:
            return self.store.workspace_id(actor)
        return self.store.identity.default_workspace

    def _actor_id(self, actor) -> str:
        if isinstance(actor, dict) and actor.get("id"):
            return str(actor["id"])
        if isinstance(actor, str) and actor:
            return actor
        return "system"

    def _ensure_seed(self, workspace_id: str) -> None:
        timestamp = now()
        with self.store.db() as connection:
            for sensor in DEFAULT_SENSORS:
                internal_id = stable_id("sensor", workspace_id, sensor["id"])
                connection.execute(
                    """INSERT INTO sensor_registry(id,workspace_id,logical_id,domain,provider,transport,authority,enabled,configuration,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(workspace_id,logical_id) DO NOTHING""",
                    (internal_id, workspace_id, sensor["id"], sensor["domain"], sensor["provider"], sensor["transport"], sensor["authority"], int(sensor["enabled"]), "{}", timestamp, timestamp),
                )

    def sensors(self, actor=None, domain="") -> list[dict[str, Any]]:
        workspace_id = self._workspace(actor)
        self._ensure_seed(workspace_id)
        sql = "SELECT * FROM sensor_registry WHERE workspace_id=?"
        args: list[Any] = [workspace_id]
        if domain:
            sql += " AND domain=?"
            args.append(str(domain).strip().lower())
        sql += " ORDER BY domain,logical_id"
        with self.store.db() as connection:
            rows = connection.execute(sql, args).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["internal_id"] = item.pop("id")
            item["id"] = item.pop("logical_id")
            item["enabled"] = bool(item["enabled"])
            item["configuration"] = json.loads(item.get("configuration") or "{}")
            output.append(item)
        return output

    def register(self, actor, payload: dict[str, Any]) -> dict[str, Any]:
        workspace_id = self._workspace(actor)
        sensor_id = str(payload.get("id") or "").strip().lower()
        domain = str(payload.get("domain") or "").strip().lower()
        provider = str(payload.get("provider") or "").strip()
        if not sensor_id or not domain or not provider:
            raise ValueError("id, domain and provider required")
        timestamp = now()
        configuration = json.dumps(payload.get("configuration") or {}, separators=(",", ":"), sort_keys=True)
        internal_id = stable_id("sensor", workspace_id, sensor_id)
        with self.store.db() as connection:
            connection.execute(
                """INSERT INTO sensor_registry(id,workspace_id,logical_id,domain,provider,transport,authority,enabled,configuration,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(workspace_id,logical_id) DO UPDATE SET domain=excluded.domain,provider=excluded.provider,
                transport=excluded.transport,authority=excluded.authority,enabled=excluded.enabled,configuration=excluded.configuration,updated_at=excluded.updated_at""",
                (internal_id, workspace_id, sensor_id, domain, provider, payload.get("transport", "https"), payload.get("authority", "secondary"), int(bool(payload.get("enabled", True))), configuration, timestamp, timestamp),
            )
        self.store.identity.audit(workspace_id, self._actor_id(actor), "sensor.registered", "sensor", sensor_id)
        return next(item for item in self.sensors(actor) if item["id"] == sensor_id)

    def record_health(self, actor, sensor_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        workspace_id = self._workspace(actor)
        status = str(payload.get("status", "unknown")).lower()
        if status not in self.VALID_STATUSES:
            raise ValueError("invalid sensor status")
        latency_ms = max(0, int(payload.get("latency_ms") or 0))
        records_seen = max(0, int(payload.get("records_seen") or 0))
        error_rate = float(payload.get("error_rate") or 0)
        if not 0 <= error_rate <= 1:
            raise ValueError("error_rate must be between 0 and 1")
        observed = now()
        health_id = stable_id(workspace_id, sensor_id, observed)
        with self.store.db() as connection:
            exists = connection.execute("SELECT id FROM sensor_registry WHERE logical_id=? AND workspace_id=?", (sensor_id, workspace_id)).fetchone()
            if not exists:
                raise KeyError("sensor not found")
            connection.execute(
                "INSERT INTO sensor_health(id,workspace_id,sensor_id,status,latency_ms,records_seen,error_rate,last_success_at,last_error,observed_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (health_id, workspace_id, sensor_id, status, latency_ms, records_seen, error_rate, payload.get("last_success_at") or (observed if status == "live" else None), str(payload.get("last_error") or "")[:500], observed),
            )
        return self.health(actor, sensor_id)

    def health(self, actor=None, sensor_id="") -> dict[str, Any]:
        workspace_id = self._workspace(actor)
        sensors = self.sensors(actor)
        if sensor_id:
            sensors = [item for item in sensors if item["id"] == sensor_id]
            if not sensors:
                raise KeyError("sensor not found")
        output = []
        with self.store.db() as connection:
            for sensor in sensors:
                row = connection.execute("SELECT * FROM sensor_health WHERE workspace_id=? AND sensor_id=? ORDER BY observed_at DESC LIMIT 1", (workspace_id, sensor["id"])).fetchone()
                item = dict(sensor)
                item["health"] = dict(row) if row else {"status": "unknown", "records_seen": 0, "error_rate": 0.0}
                output.append(item)
        return {"sensors": output, "summary": self.coverage(actor)}

    def ingest_observations(self, actor, sensor_id: str, observations: list[dict[str, Any]]) -> dict[str, Any]:
        if not isinstance(observations, list):
            raise ValueError("observations must be a list")
        workspace_id = self._workspace(actor)
        sensor = next((item for item in self.sensors(actor) if item["id"] == sensor_id), None)
        if not sensor:
            raise KeyError("sensor not found")
        accepted = duplicates = rejected = 0
        with self.store.db() as connection:
            for raw in observations:
                if not isinstance(raw, dict):
                    rejected += 1
                    continue
                title = str(raw.get("title") or "").strip()
                observed_at = str(raw.get("observed_at") or now())
                latitude = raw.get("latitude")
                longitude = raw.get("longitude")
                if not title or (latitude is not None and not -90 <= float(latitude) <= 90) or (longitude is not None and not -180 <= float(longitude) <= 180):
                    rejected += 1
                    continue
                external_id = str(raw.get("external_id") or raw.get("id") or "")
                dedupe_key = str(raw.get("dedupe_key") or stable_id(sensor_id, external_id, title, observed_at[:16]))
                observation_id = stable_id(workspace_id, sensor_id, dedupe_key)
                try:
                    connection.execute(
                        "INSERT INTO sensor_observations(id,workspace_id,sensor_id,domain,external_id,observed_at,latitude,longitude,severity,title,payload,dedupe_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (observation_id, workspace_id, sensor_id, sensor["domain"], external_id, observed_at, latitude, longitude, raw.get("severity", "unknown"), title, json.dumps(raw, separators=(",", ":"), sort_keys=True), dedupe_key),
                    )
                    accepted += 1
                except Exception as exc:
                    if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                        duplicates += 1
                    else:
                        raise
        self.store.identity.audit(workspace_id, self._actor_id(actor), "sensor.ingest", "sensor", sensor_id, metadata={"accepted": accepted, "duplicates": duplicates, "rejected": rejected})
        return {"sensor_id": sensor_id, "accepted": accepted, "duplicates_suppressed": duplicates, "rejected": rejected}

    def observations(self, actor=None, domain="", limit=100) -> list[dict[str, Any]]:
        workspace_id = self._workspace(actor)
        sql = "SELECT * FROM sensor_observations WHERE workspace_id=?"
        args: list[Any] = [workspace_id]
        if domain:
            sql += " AND domain=?"
            args.append(str(domain).strip().lower())
        sql += " ORDER BY observed_at DESC LIMIT ?"
        args.append(max(1, min(1000, int(limit))))
        with self.store.db() as connection:
            rows = connection.execute(sql, args).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.get("payload") or "{}")
            output.append(item)
        return output

    def coverage(self, actor=None) -> dict[str, Any]:
        sensors = self.sensors(actor)
        domains = sorted({sensor["domain"] for sensor in sensors})
        enabled = [sensor for sensor in sensors if sensor["enabled"]]
        workspace_id = self._workspace(actor)
        live = degraded = offline = unknown = 0
        with self.store.db() as connection:
            for sensor in enabled:
                row = connection.execute("SELECT status FROM sensor_health WHERE workspace_id=? AND sensor_id=? ORDER BY observed_at DESC LIMIT 1", (workspace_id, sensor["id"])).fetchone()
                status = row["status"] if row else "unknown"
                if status == "live":
                    live += 1
                elif status == "degraded":
                    degraded += 1
                elif status == "offline":
                    offline += 1
                else:
                    unknown += 1
        denominator = max(1, len(sensors))
        readiness = round((len(enabled) / denominator) * 50 + (live / denominator) * 50, 1)
        return {"phase": 15, "domains_registered": len(domains), "sensors_registered": len(sensors), "sensors_enabled": len(enabled), "live": live, "degraded": degraded, "offline": offline, "unknown": unknown, "mesh_readiness_score": readiness, "domains": domains}
