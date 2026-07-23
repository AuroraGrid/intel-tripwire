from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from phase15_mesh import stable_id

MOBILE_ASSET_TYPES = {"AIRCRAFT", "VESSEL", "SATELLITE"}
SENSITIVITY_LEVELS = {"PUBLIC", "DELAYED", "AGGREGATED", "RESTRICTED"}
INFRASTRUCTURE_TYPES = {
    "AIRPORT", "PORT", "PIPELINE", "LNG_TERMINAL", "REFINERY",
    "POWER_STATION", "DATA_CENTER", "SUBMARINE_CABLE", "INTERNET_EXCHANGE",
    "BORDER_CROSSING", "RAIL_CORRIDOR", "ROAD", "MILITARY_SITE",
    "NUCLEAR_SITE", "OTHER",
}
ANOMALY_TYPES = {
    "GPS_INTERFERENCE", "DARK_GAP", "LOITERING", "HOLDING",
    "ROUTE_DEVIATION", "SANCTIONS_EXPOSURE", "PORT_CONGESTION",
    "AIRSPACE_RESTRICTION", "DATA_STALE",
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time(value: Any) -> datetime:
    text = str(value or now()).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid ISO timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def loads(value: Any, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except (TypeError, json.JSONDecodeError):
        return default


def haversine_km(first: tuple[float, float], second: tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, first)
    lat2, lon2 = map(math.radians, second)
    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1
    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return 6371.0088 * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


class LiveOperatingPicture:
    """Workspace-isolated mobility and infrastructure operating picture.

    The public query surface enforces delayed/restricted tracking policy in SQL.
    Structured anomaly inputs are preserved as claims, not silently promoted to facts.
    """

    def __init__(self, store, mesh):
        self.store = store
        self.mesh = mesh
        self.default_sensitive_delay = max(
            3600, int(os.getenv("AURORA_SENSITIVE_TRACK_DELAY_SECONDS", "86400"))
        )
        self.stale_seconds = max(
            60, int(os.getenv("AURORA_TRACK_STALE_SECONDS", "900"))
        )
        self._init_schema()

    def _init_schema(self) -> None:
        with self.store.db() as connection:
            connection.executescript("""
            CREATE TABLE IF NOT EXISTS operating_assets(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, asset_type TEXT NOT NULL,
                provider TEXT NOT NULL, external_id TEXT NOT NULL, display_name TEXT NOT NULL,
                country_code TEXT, asset_class TEXT, sensitivity TEXT NOT NULL,
                status TEXT NOT NULL, attributes TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(workspace_id,asset_type,provider,external_id)
            );
            CREATE TABLE IF NOT EXISTS operating_positions(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, asset_id TEXT NOT NULL,
                observed_at TEXT NOT NULL, received_at TEXT NOT NULL,
                latitude REAL NOT NULL, longitude REAL NOT NULL, altitude REAL,
                speed REAL, heading REAL, source_sensor TEXT,
                source_observation_id TEXT NOT NULL, visible_after TEXT NOT NULL,
                quality REAL NOT NULL, attributes TEXT NOT NULL,
                UNIQUE(workspace_id,asset_id,source_observation_id)
            );
            CREATE TABLE IF NOT EXISTS infrastructure_assets(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
                infrastructure_type TEXT NOT NULL, provider TEXT NOT NULL,
                external_id TEXT NOT NULL, name TEXT NOT NULL, country_code TEXT,
                latitude REAL, longitude REAL, geometry TEXT NOT NULL,
                status TEXT NOT NULL, sensitivity TEXT NOT NULL,
                attributes TEXT NOT NULL, source_url TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(workspace_id,infrastructure_type,provider,external_id)
            );
            CREATE TABLE IF NOT EXISTS operating_anomalies(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, asset_id TEXT,
                infrastructure_id TEXT, anomaly_type TEXT NOT NULL,
                observed_at TEXT NOT NULL, severity TEXT NOT NULL,
                state TEXT NOT NULL, summary TEXT NOT NULL,
                evidence TEXT NOT NULL, source_observation_id TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(workspace_id,anomaly_type,source_observation_id)
            );
            CREATE TABLE IF NOT EXISTS operating_revisions(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
                resource_type TEXT NOT NULL, resource_id TEXT NOT NULL,
                action TEXT NOT NULL, before_state TEXT NOT NULL,
                after_state TEXT NOT NULL, reason TEXT NOT NULL,
                created_by TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_operating_assets_lookup
                ON operating_assets(workspace_id,asset_type,sensitivity,status,updated_at);
            CREATE INDEX IF NOT EXISTS idx_operating_positions_map
                ON operating_positions(workspace_id,visible_after,observed_at,latitude,longitude);
            CREATE INDEX IF NOT EXISTS idx_operating_positions_asset
                ON operating_positions(workspace_id,asset_id,observed_at);
            CREATE INDEX IF NOT EXISTS idx_infrastructure_map
                ON infrastructure_assets(workspace_id,infrastructure_type,status,latitude,longitude);
            CREATE INDEX IF NOT EXISTS idx_operating_anomalies_queue
                ON operating_anomalies(workspace_id,state,severity,observed_at);
            """)

    def _workspace(self, actor: dict[str, Any]) -> str:
        return str(actor["workspace_id"])

    def _actor(self, actor: dict[str, Any]) -> str:
        return str(actor.get("id") or "system")

    def _revision(
        self, actor: dict[str, Any], resource_type: str, resource_id: str,
        action: str, before: Any, after: Any, reason: str,
    ) -> None:
        stamp = now()
        revision_id = stable_id(
            "operating-revision", self._workspace(actor), resource_type,
            resource_id, action, stamp,
        )
        with self.store.db() as connection:
            connection.execute(
                """INSERT INTO operating_revisions(
                id,workspace_id,resource_type,resource_id,action,before_state,
                after_state,reason,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    revision_id, self._workspace(actor), resource_type, resource_id,
                    action, json.dumps(before or {}, sort_keys=True),
                    json.dumps(after or {}, sort_keys=True), str(reason or ""),
                    self._actor(actor), stamp,
                ),
            )

    def _coordinates(self, payload: dict[str, Any]) -> tuple[float, float]:
        try:
            latitude = float(payload.get("latitude"))
            longitude = float(payload.get("longitude"))
        except (TypeError, ValueError) as exc:
            raise ValueError("latitude and longitude required") from exc
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ValueError("coordinates out of range")
        return latitude, longitude

    def upsert_asset(
        self, actor: dict[str, Any], payload: dict[str, Any]
    ) -> dict[str, Any]:
        asset_type = str(payload.get("asset_type") or "").upper()
        provider = str(payload.get("provider") or "").strip()
        external_id = str(payload.get("external_id") or "").strip()
        if asset_type not in MOBILE_ASSET_TYPES:
            raise ValueError("invalid asset_type")
        if not provider or not external_id:
            raise ValueError("provider and external_id required")
        sensitivity = str(payload.get("sensitivity") or "PUBLIC").upper()
        if sensitivity not in SENSITIVITY_LEVELS:
            raise ValueError("invalid sensitivity")
        if bool(payload.get("military")) and sensitivity == "PUBLIC":
            sensitivity = "DELAYED"
        asset_id = stable_id(
            "operating-asset", self._workspace(actor), asset_type, provider, external_id
        )
        stamp = now()
        with self.store.db() as connection:
            row = connection.execute(
                "SELECT * FROM operating_assets WHERE id=? AND workspace_id=?",
                (asset_id, self._workspace(actor)),
            ).fetchone()
            before = dict(row) if row else {}
            connection.execute(
                """INSERT INTO operating_assets(
                id,workspace_id,asset_type,provider,external_id,display_name,
                country_code,asset_class,sensitivity,status,attributes,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(workspace_id,asset_type,provider,external_id) DO UPDATE SET
                display_name=excluded.display_name,country_code=excluded.country_code,
                asset_class=excluded.asset_class,sensitivity=excluded.sensitivity,
                status=excluded.status,attributes=excluded.attributes,updated_at=excluded.updated_at""",
                (
                    asset_id, self._workspace(actor), asset_type, provider, external_id,
                    str(payload.get("display_name") or external_id),
                    payload.get("country_code"), payload.get("asset_class"),
                    sensitivity, str(payload.get("status") or "ACTIVE").upper(),
                    json.dumps(payload.get("attributes") or {}, sort_keys=True),
                    stamp, stamp,
                ),
            )
        item = self.asset(actor, asset_id)
        if not before or before.get("sensitivity") != item["sensitivity"]:
            self._revision(
                actor, "mobile_asset", asset_id, "UPSERT", before, item,
                str(payload.get("reason") or "Mobile asset upsert"),
            )
        return item

    def asset(self, actor: dict[str, Any], asset_id: str) -> dict[str, Any]:
        with self.store.db() as connection:
            row = connection.execute(
                "SELECT * FROM operating_assets WHERE id=? AND workspace_id=?",
                (asset_id, self._workspace(actor)),
            ).fetchone()
        if not row:
            raise KeyError("operating asset not found")
        item = dict(row)
        item["attributes"] = loads(item["attributes"], {})
        return item

    def ingest_position(
        self, actor: dict[str, Any], asset_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        asset = self.asset(actor, asset_id)
        latitude, longitude = self._coordinates(payload)
        observed = parse_time(payload.get("observed_at"))
        source_observation_id = str(
            payload.get("source_observation_id")
            or stable_id(
                asset_id, iso(observed), f"{latitude:.5f}", f"{longitude:.5f}"
            )
        )
        delay = 0
        if asset["sensitivity"] in {"DELAYED", "AGGREGATED"}:
            delay = max(
                self.default_sensitive_delay,
                int(payload.get("delay_seconds") or self.default_sensitive_delay),
            )
        elif asset["sensitivity"] == "RESTRICTED":
            delay = 10 * 365 * 24 * 3600
        visible_after = iso(observed + timedelta(seconds=delay))
        quality = float(payload.get("quality", 1.0))
        if not 0 <= quality <= 1:
            raise ValueError("quality must be between 0 and 1")
        position_id = stable_id(
            "operating-position", self._workspace(actor), asset_id,
            source_observation_id,
        )
        stamp = now()
        with self.store.db() as connection:
            connection.execute(
                """INSERT INTO operating_positions(
                id,workspace_id,asset_id,observed_at,received_at,latitude,longitude,
                altitude,speed,heading,source_sensor,source_observation_id,visible_after,
                quality,attributes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(workspace_id,asset_id,source_observation_id) DO NOTHING""",
                (
                    position_id, self._workspace(actor), asset_id, iso(observed), stamp,
                    latitude, longitude, payload.get("altitude"), payload.get("speed"),
                    payload.get("heading"), payload.get("source_sensor"),
                    source_observation_id, visible_after, quality,
                    json.dumps(payload.get("attributes") or {}, sort_keys=True),
                ),
            )
            row = connection.execute(
                "SELECT * FROM operating_positions WHERE id=? AND workspace_id=?",
                (position_id, self._workspace(actor)),
            ).fetchone()
            connection.execute(
                "UPDATE operating_assets SET updated_at=? WHERE id=? AND workspace_id=?",
                (stamp, asset_id, self._workspace(actor)),
            )
        result = dict(row)
        result["attributes"] = loads(result["attributes"], {})
        result["publicly_visible"] = (
            asset["sensitivity"] != "RESTRICTED" and result["visible_after"] <= now()
        )
        self._structured_anomalies(actor, asset, result, payload)
        return result

    def _structured_anomalies(
        self, actor: dict[str, Any], asset: dict[str, Any],
        position: dict[str, Any], payload: dict[str, Any],
    ) -> None:
        candidates = []
        if bool(payload.get("gps_interference")):
            candidates.append(("GPS_INTERFERENCE", "high", "Provider reported GPS interference"))
        if float(payload.get("ais_gap_hours") or 0) >= 6:
            candidates.append(("DARK_GAP", "high", "AIS reporting gap exceeded six hours"))
        if (
            asset["asset_type"] == "VESSEL"
            and float(payload.get("loitering_minutes") or 0) >= 60
            and float(payload.get("speed") or 0) <= 1
        ):
            candidates.append(("LOITERING", "medium", "Low-speed loitering exceeded one hour"))
        if (
            asset["asset_type"] == "AIRCRAFT"
            and float(payload.get("holding_minutes") or 0) >= 20
        ):
            candidates.append(("HOLDING", "medium", "Aircraft holding pattern exceeded twenty minutes"))
        if bool(payload.get("route_deviation")):
            candidates.append(("ROUTE_DEVIATION", "medium", "Provider reported route deviation"))
        if bool(payload.get("sanctions_match")):
            candidates.append(("SANCTIONS_EXPOSURE", "critical", "Asset matched a sanctions record"))
        for anomaly_type, severity, summary in candidates:
            self.record_anomaly(actor, {
                "asset_id": asset["id"],
                "anomaly_type": anomaly_type,
                "observed_at": position["observed_at"],
                "severity": severity,
                "summary": summary,
                "source_observation_id": position["source_observation_id"],
                "evidence": {
                    "structured_provider_fields": {
                        key: payload.get(key) for key in (
                            "gps_interference", "ais_gap_hours", "loitering_minutes",
                            "holding_minutes", "route_deviation", "sanctions_match",
                        ) if key in payload
                    },
                    "position_id": position["id"],
                },
            })

    def record_anomaly(
        self, actor: dict[str, Any], payload: dict[str, Any]
    ) -> dict[str, Any]:
        anomaly_type = str(payload.get("anomaly_type") or "").upper()
        if anomaly_type not in ANOMALY_TYPES:
            raise ValueError("invalid anomaly_type")
        source_id = str(payload.get("source_observation_id") or "").strip()
        if not source_id:
            raise ValueError("source_observation_id required")
        asset_id = payload.get("asset_id")
        infrastructure_id = payload.get("infrastructure_id")
        if asset_id:
            self.asset(actor, str(asset_id))
        if infrastructure_id:
            self.infrastructure(actor, str(infrastructure_id))
        anomaly_id = stable_id(
            "operating-anomaly", self._workspace(actor), anomaly_type, source_id
        )
        stamp = now()
        with self.store.db() as connection:
            connection.execute(
                """INSERT INTO operating_anomalies(
                id,workspace_id,asset_id,infrastructure_id,anomaly_type,observed_at,
                severity,state,summary,evidence,source_observation_id,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,'OPEN',?,?,?,?,?)
                ON CONFLICT(workspace_id,anomaly_type,source_observation_id) DO NOTHING""",
                (
                    anomaly_id, self._workspace(actor), asset_id, infrastructure_id,
                    anomaly_type, str(payload.get("observed_at") or stamp),
                    str(payload.get("severity") or "medium").lower(),
                    str(payload.get("summary") or anomaly_type),
                    json.dumps(payload.get("evidence") or {}, sort_keys=True),
                    source_id, stamp, stamp,
                ),
            )
            row = connection.execute(
                "SELECT * FROM operating_anomalies WHERE id=? AND workspace_id=?",
                (anomaly_id, self._workspace(actor)),
            ).fetchone()
        item = dict(row)
        item["evidence"] = loads(item["evidence"], {})
        return item

    def review_anomaly(
        self, actor: dict[str, Any], anomaly_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        state = str(payload.get("state") or "").upper()
        if state not in {"ACKNOWLEDGED", "RESOLVED", "DISMISSED", "OPEN"}:
            raise ValueError("invalid anomaly state")
        with self.store.db() as connection:
            row = connection.execute(
                "SELECT * FROM operating_anomalies WHERE id=? AND workspace_id=?",
                (anomaly_id, self._workspace(actor)),
            ).fetchone()
            if not row:
                raise KeyError("anomaly not found")
            before = dict(row)
            connection.execute(
                "UPDATE operating_anomalies SET state=?,updated_at=? "
                "WHERE id=? AND workspace_id=?",
                (state, now(), anomaly_id, self._workspace(actor)),
            )
            updated = connection.execute(
                "SELECT * FROM operating_anomalies WHERE id=? AND workspace_id=?",
                (anomaly_id, self._workspace(actor)),
            ).fetchone()
        item = dict(updated)
        item["evidence"] = loads(item["evidence"], {})
        self._revision(
            actor, "anomaly", anomaly_id, "REVIEW", before, item,
            str(payload.get("rationale") or "Analyst anomaly review"),
        )
        return item

    def upsert_infrastructure(
        self, actor: dict[str, Any], payload: dict[str, Any]
    ) -> dict[str, Any]:
        infrastructure_type = str(payload.get("infrastructure_type") or "OTHER").upper()
        if infrastructure_type not in INFRASTRUCTURE_TYPES:
            raise ValueError("invalid infrastructure_type")
        provider = str(payload.get("provider") or "").strip()
        external_id = str(payload.get("external_id") or "").strip()
        name = str(payload.get("name") or "").strip()
        if not provider or not external_id or not name:
            raise ValueError("provider, external_id and name required")
        sensitivity = str(payload.get("sensitivity") or "PUBLIC").upper()
        if sensitivity not in SENSITIVITY_LEVELS:
            raise ValueError("invalid sensitivity")
        latitude = payload.get("latitude")
        longitude = payload.get("longitude")
        if latitude is not None or longitude is not None:
            latitude, longitude = self._coordinates(payload)
        geometry = payload.get("geometry") or (
            {"type": "Point", "coordinates": [longitude, latitude]}
            if latitude is not None else {}
        )
        infrastructure_id = stable_id(
            "infrastructure", self._workspace(actor), infrastructure_type,
            provider, external_id,
        )
        stamp = now()
        with self.store.db() as connection:
            connection.execute(
                """INSERT INTO infrastructure_assets(
                id,workspace_id,infrastructure_type,provider,external_id,name,
                country_code,latitude,longitude,geometry,status,sensitivity,
                attributes,source_url,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(workspace_id,infrastructure_type,provider,external_id)
                DO UPDATE SET name=excluded.name,country_code=excluded.country_code,
                latitude=excluded.latitude,longitude=excluded.longitude,
                geometry=excluded.geometry,status=excluded.status,
                sensitivity=excluded.sensitivity,attributes=excluded.attributes,
                source_url=excluded.source_url,updated_at=excluded.updated_at""",
                (
                    infrastructure_id, self._workspace(actor), infrastructure_type,
                    provider, external_id, name, payload.get("country_code"),
                    latitude, longitude, json.dumps(geometry, sort_keys=True),
                    str(payload.get("status") or "ACTIVE").upper(), sensitivity,
                    json.dumps(payload.get("attributes") or {}, sort_keys=True),
                    payload.get("source_url"), stamp, stamp,
                ),
            )
        return self.infrastructure(actor, infrastructure_id)

    def infrastructure(
        self, actor: dict[str, Any], infrastructure_id: str
    ) -> dict[str, Any]:
        with self.store.db() as connection:
            row = connection.execute(
                "SELECT * FROM infrastructure_assets WHERE id=? AND workspace_id=?",
                (infrastructure_id, self._workspace(actor)),
            ).fetchone()
        if not row:
            raise KeyError("infrastructure not found")
        item = dict(row)
        item["geometry"] = loads(item["geometry"], {})
        item["attributes"] = loads(item["attributes"], {})
        return item

    def positions(
        self, actor: dict[str, Any], filters: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        filters = filters or {}
        sql = """SELECT p.*,a.asset_type,a.display_name,a.provider,a.external_id,
                 a.asset_class,a.country_code,a.sensitivity,a.status asset_status
                 FROM operating_positions p JOIN operating_assets a ON a.id=p.asset_id
                 WHERE p.workspace_id=? AND a.workspace_id=?
                 AND a.sensitivity!='RESTRICTED' AND p.visible_after<=?"""
        args: list[Any] = [self._workspace(actor), self._workspace(actor), now()]
        if filters.get("asset_type"):
            sql += " AND a.asset_type=?"
            args.append(str(filters["asset_type"]).upper())
        if filters.get("after"):
            sql += " AND p.observed_at>=?"
            args.append(str(filters["after"]))
        for name, operator, column in (
            ("min_lat", ">=", "p.latitude"), ("max_lat", "<=", "p.latitude"),
            ("min_lon", ">=", "p.longitude"), ("max_lon", "<=", "p.longitude"),
        ):
            if filters.get(name) not in (None, ""):
                sql += f" AND {column}{operator}?"
                args.append(float(filters[name]))
        sql += " ORDER BY p.observed_at DESC LIMIT ?"
        args.append(max(1, min(10000, int(filters.get("limit") or 1000))))
        with self.store.db() as connection:
            rows = connection.execute(sql, args).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["attributes"] = loads(item["attributes"], {})
            item["stale"] = (
                parse_time(item["observed_at"])
                < datetime.now(timezone.utc) - timedelta(seconds=self.stale_seconds)
            )
            output.append(item)
        return output

    def track(
        self, actor: dict[str, Any], asset_id: str, limit: int = 1000
    ) -> dict[str, Any]:
        asset = self.asset(actor, asset_id)
        if asset["sensitivity"] == "RESTRICTED":
            raise KeyError("track not available")
        with self.store.db() as connection:
            rows = connection.execute(
                """SELECT * FROM operating_positions WHERE workspace_id=? AND asset_id=?
                AND visible_after<=? ORDER BY observed_at DESC LIMIT ?""",
                (
                    self._workspace(actor), asset_id, now(),
                    max(1, min(10000, int(limit))),
                ),
            ).fetchall()
        positions = []
        for row in reversed(rows):
            item = dict(row)
            item["attributes"] = loads(item["attributes"], {})
            positions.append(item)
        distance = sum(
            haversine_km(
                (positions[index - 1]["latitude"], positions[index - 1]["longitude"]),
                (positions[index]["latitude"], positions[index]["longitude"]),
            )
            for index in range(1, len(positions))
        )
        return {"asset": asset, "positions": positions, "distance_km": round(distance, 2)}

    def infrastructure_list(
        self, actor: dict[str, Any], filters: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        filters = filters or {}
        sql = """SELECT id FROM infrastructure_assets WHERE workspace_id=?
                 AND sensitivity!='RESTRICTED'"""
        args: list[Any] = [self._workspace(actor)]
        if filters.get("infrastructure_type"):
            sql += " AND infrastructure_type=?"
            args.append(str(filters["infrastructure_type"]).upper())
        if filters.get("country_code"):
            sql += " AND country_code=?"
            args.append(str(filters["country_code"]).upper())
        sql += " ORDER BY updated_at DESC LIMIT ?"
        args.append(max(1, min(10000, int(filters.get("limit") or 1000))))
        with self.store.db() as connection:
            rows = connection.execute(sql, args).fetchall()
        return [self.infrastructure(actor, row["id"]) for row in rows]

    def anomalies(
        self, actor: dict[str, Any], state: str = "OPEN", limit: int = 100
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM operating_anomalies WHERE workspace_id=?"
        args: list[Any] = [self._workspace(actor)]
        if state:
            sql += " AND state=?"
            args.append(str(state).upper())
        sql += " ORDER BY observed_at DESC LIMIT ?"
        args.append(max(1, min(1000, int(limit))))
        with self.store.db() as connection:
            rows = connection.execute(sql, args).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["evidence"] = loads(item["evidence"], {})
            output.append(item)
        return output

    def geojson(
        self, actor: dict[str, Any], filters: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        filters = filters or {}
        features = []
        for item in self.positions(actor, filters):
            features.append({
                "type": "Feature",
                "id": item["id"],
                "geometry": {
                    "type": "Point",
                    "coordinates": [item["longitude"], item["latitude"]],
                },
                "properties": {
                    "kind": "MOBILE_ASSET", "asset_id": item["asset_id"],
                    "asset_type": item["asset_type"], "display_name": item["display_name"],
                    "observed_at": item["observed_at"], "altitude": item["altitude"],
                    "speed": item["speed"], "heading": item["heading"],
                    "stale": item["stale"], "sensitivity": item["sensitivity"],
                },
            })
        if str(filters.get("include_infrastructure", "true")).lower() != "false":
            for item in self.infrastructure_list(actor, filters):
                if not item["geometry"]:
                    continue
                features.append({
                    "type": "Feature", "id": item["id"],
                    "geometry": item["geometry"],
                    "properties": {
                        "kind": "INFRASTRUCTURE",
                        "infrastructure_type": item["infrastructure_type"],
                        "name": item["name"], "status": item["status"],
                        "country_code": item["country_code"],
                    },
                })
        return {
            "type": "FeatureCollection",
            "features": features,
            "generated_at": now(),
            "safety": "Restricted and not-yet-delayed tracks are excluded",
        }

    def process_mesh(
        self, actor: dict[str, Any], limit: int = 1000
    ) -> dict[str, Any]:
        totals = {
            "observations_seen": 0, "positions_materialized": 0,
            "infrastructure_materialized": 0, "rejected": 0,
        }
        for domain in ("aviation", "maritime", "infrastructure"):
            observations = list(reversed(self.mesh.observations(actor, domain, limit)))
            for observation in observations:
                totals["observations_seen"] += 1
                payload = observation.get("payload") or {}
                try:
                    if domain in {"aviation", "maritime"}:
                        asset_type = "AIRCRAFT" if domain == "aviation" else "VESSEL"
                        external_id = str(
                            payload.get("icao24") or payload.get("mmsi")
                            or observation.get("external_id") or ""
                        )
                        if not external_id:
                            raise ValueError("asset external identifier missing")
                        asset = self.upsert_asset(actor, {
                            "asset_type": asset_type,
                            "provider": payload.get("provider") or observation["sensor_id"],
                            "external_id": external_id,
                            "display_name": payload.get("callsign") or payload.get("name") or external_id,
                            "country_code": payload.get("country_code"),
                            "asset_class": payload.get("asset_class"),
                            "sensitivity": payload.get("sensitivity") or "PUBLIC",
                            "military": bool(payload.get("military")),
                            "attributes": payload.get("asset_attributes") or {},
                        })
                        self.ingest_position(actor, asset["id"], {
                            **payload,
                            "latitude": observation["latitude"],
                            "longitude": observation["longitude"],
                            "observed_at": observation["observed_at"],
                            "source_sensor": observation["sensor_id"],
                            "source_observation_id": observation["id"],
                        })
                        totals["positions_materialized"] += 1
                    else:
                        self.upsert_infrastructure(actor, {
                            **payload,
                            "provider": payload.get("provider") or observation["sensor_id"],
                            "external_id": payload.get("external_id") or observation["id"],
                            "name": payload.get("name") or observation["title"],
                            "latitude": observation.get("latitude"),
                            "longitude": observation.get("longitude"),
                        })
                        totals["infrastructure_materialized"] += 1
                except (ValueError, KeyError, TypeError):
                    totals["rejected"] += 1
        return totals

    def scorecard(self, actor: dict[str, Any]) -> dict[str, Any]:
        workspace_id = self._workspace(actor)
        with self.store.db() as connection:
            assets = connection.execute(
                "SELECT asset_type,COUNT(*) total FROM operating_assets "
                "WHERE workspace_id=? GROUP BY asset_type", (workspace_id,)
            ).fetchall()
            sensitive = connection.execute(
                "SELECT sensitivity,COUNT(*) total FROM operating_assets "
                "WHERE workspace_id=? GROUP BY sensitivity", (workspace_id,)
            ).fetchall()
            infrastructure = connection.execute(
                "SELECT infrastructure_type,COUNT(*) total FROM infrastructure_assets "
                "WHERE workspace_id=? GROUP BY infrastructure_type", (workspace_id,)
            ).fetchall()
            latest = connection.execute(
                "SELECT MAX(observed_at) value FROM operating_positions WHERE workspace_id=?",
                (workspace_id,),
            ).fetchone()
        return {
            "phase": 20,
            "mobile_assets": {row["asset_type"]: int(row["total"]) for row in assets},
            "sensitivity": {row["sensitivity"]: int(row["total"]) for row in sensitive},
            "infrastructure": {
                row["infrastructure_type"]: int(row["total"]) for row in infrastructure
            },
            "open_anomalies": len(self.anomalies(actor, "OPEN", 1000)),
            "latest_position_at": latest["value"] if latest else None,
            "public_tracking_policy": {
                "default_sensitive_delay_seconds": self.default_sensitive_delay,
                "restricted_excluded": True,
            },
        }
