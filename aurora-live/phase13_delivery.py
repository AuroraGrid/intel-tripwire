from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone

from storage import sid

LAYER_TYPES = {"points", "lines", "polygons", "heatmap", "tracks", "timeline"}


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def loads(value, default):
    try:
        return json.loads(value) if value else default
    except (TypeError, json.JSONDecodeError):
        return default


class DeliveryLayer:
    def __init__(self, store, fusion):
        self.store = store
        self.fusion = fusion
        self.init()

    def init(self):
        with self.store.db() as connection:
            connection.executescript("""
            CREATE TABLE IF NOT EXISTS map_layers(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, name TEXT NOT NULL,
                slug TEXT NOT NULL, layer_type TEXT NOT NULL, source_types TEXT NOT NULL,
                style TEXT NOT NULL, enabled INTEGER NOT NULL, created_at TEXT NOT NULL,
                UNIQUE(workspace_id,slug)
            );
            CREATE TABLE IF NOT EXISTS map_snapshots(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, layer_id TEXT NOT NULL,
                feature_count INTEGER NOT NULL, geojson TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS api_keys(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, name TEXT NOT NULL,
                prefix TEXT NOT NULL, token_hash TEXT NOT NULL, active INTEGER NOT NULL,
                created_by TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_map_layers_workspace ON map_layers(workspace_id,enabled,name);
            CREATE INDEX IF NOT EXISTS idx_map_snapshots_layer ON map_snapshots(workspace_id,layer_id,created_at);
            """)

    def create_layer(self, actor, payload):
        name = str(payload.get("name", "")).strip()
        slug = str(payload.get("slug", "")).strip().lower().replace(" ", "-")
        layer_type = str(payload.get("layer_type", "points")).strip().lower()
        source_types = payload.get("source_types") or []
        style = payload.get("style") or {}
        if not name or not slug:
            raise ValueError("name and slug required")
        if layer_type not in LAYER_TYPES:
            raise ValueError("invalid layer_type")
        if not isinstance(source_types, list):
            raise ValueError("source_types must be a list")
        if not isinstance(style, dict):
            raise ValueError("style must be an object")
        layer_id = sid("map-layer", actor["workspace_id"], slug)
        with self.store.db() as connection:
            connection.execute("""INSERT INTO map_layers(id,workspace_id,name,slug,layer_type,source_types,style,enabled,created_at)
            VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(workspace_id,slug) DO UPDATE SET
            name=excluded.name,layer_type=excluded.layer_type,source_types=excluded.source_types,style=excluded.style,enabled=excluded.enabled""",
            (layer_id, actor["workspace_id"], name, slug, layer_type, dumps(source_types), dumps(style), 1 if payload.get("enabled", True) else 0, now()))
        return self.layer(actor, layer_id)

    def layer(self, actor, layer_id):
        with self.store.db() as connection:
            row = connection.execute("SELECT * FROM map_layers WHERE id=? AND workspace_id=?", (layer_id, actor["workspace_id"])).fetchone()
        if not row:
            raise KeyError("layer not found")
        item = dict(row)
        item["source_types"] = loads(item["source_types"], [])
        item["style"] = loads(item["style"], {})
        item["enabled"] = bool(item["enabled"])
        return item

    def layers(self, actor):
        with self.store.db() as connection:
            rows = connection.execute("SELECT id FROM map_layers WHERE workspace_id=? ORDER BY name", (actor["workspace_id"],)).fetchall()
        return [self.layer(actor, row["id"]) for row in rows]

    def build_snapshot(self, actor, layer_id):
        layer = self.layer(actor, layer_id)
        allowed = set(layer["source_types"])
        signals = self.fusion.list(actor, limit=1000)
        if allowed:
            signals = [item for item in signals if item["signal_type"] in allowed]
        features = []
        for item in signals:
            lat, lon = item.get("latitude"), item.get("longitude")
            if lat is None or lon is None:
                continue
            features.append({"type": "Feature", "id": item["id"], "geometry": {"type": "Point", "coordinates": [float(lon), float(lat)]},
                             "properties": {"title": item["title"], "signal_type": item["signal_type"], "provider": item["provider"], "confidence": item["confidence"], "observed_at": item["observed_at"]}})
        geojson = {"type": "FeatureCollection", "features": features}
        snapshot_id = sid("map-snapshot", actor["workspace_id"], layer_id, now(), secrets.token_hex(4))
        with self.store.db() as connection:
            connection.execute("INSERT INTO map_snapshots(id,workspace_id,layer_id,feature_count,geojson,created_at) VALUES(?,?,?,?,?,?)",
                (snapshot_id, actor["workspace_id"], layer_id, len(features), dumps(geojson), now()))
        return self.snapshot(actor, snapshot_id)

    def snapshot(self, actor, snapshot_id):
        with self.store.db() as connection:
            row = connection.execute("SELECT * FROM map_snapshots WHERE id=? AND workspace_id=?", (snapshot_id, actor["workspace_id"])).fetchone()
        if not row:
            raise KeyError("snapshot not found")
        item = dict(row); item["geojson"] = loads(item["geojson"], {"type": "FeatureCollection", "features": []})
        return item

    def latest_snapshot(self, actor, layer_id):
        self.layer(actor, layer_id)
        with self.store.db() as connection:
            row = connection.execute("SELECT id FROM map_snapshots WHERE workspace_id=? AND layer_id=? ORDER BY created_at DESC LIMIT 1", (actor["workspace_id"], layer_id)).fetchone()
        return self.snapshot(actor, row["id"]) if row else self.build_snapshot(actor, layer_id)

    def timeline(self, actor, limit=500):
        signals = self.fusion.list(actor, limit=limit)
        events = self.fusion.fused_list(actor, limit=limit)
        rows = [{"kind": "signal", "id": item["id"], "title": item["title"], "at": item["observed_at"], "category": item["signal_type"], "confidence": item["confidence"]} for item in signals]
        rows += [{"kind": "fused_event", "id": item["id"], "title": item["title"], "at": item["created_at"], "category": item["category"], "confidence": item["confidence"]} for item in events]
        return {"events": sorted(rows, key=lambda x: x["at"], reverse=True)[:max(1, min(1000, int(limit)))]}

    def openapi(self):
        paths = {
            "/api/platform/live-signals": ["get", "post"],
            "/api/platform/provider-health": ["get", "post"],
            "/api/platform/fused-events": ["get", "post"],
            "/api/platform/map-layers": ["get", "post"],
            "/api/platform/map-layers/{id}/snapshot": ["get", "post"],
            "/api/platform/timeline": ["get"],
        }
        return {"openapi": "3.1.0", "info": {"title": "AURORA LIVE API", "version": "13.0"},
                "paths": {path: {method: {"responses": {"200": {"description": "Success"}}} for method in methods} for path, methods in paths.items()}}
