from __future__ import annotations

import heapq
import json
from datetime import datetime, timezone
from typing import Any

from phase15_mesh import stable_id
from phase20_operating_picture import haversine_km

NODE_TYPES = {
    "PORT", "AIRPORT", "BORDER_CROSSING", "CHOKEPOINT", "RAIL_TERMINAL",
    "ROAD_JUNCTION", "PIPELINE_TERMINAL", "CITY", "WAREHOUSE", "OTHER",
}
MODES = {"SEA", "AIR", "ROAD", "RAIL", "PIPELINE", "MULTIMODAL"}
EDGE_STATUSES = {"OPEN", "DEGRADED", "CLOSED", "PLANNED"}
DISRUPTION_STATES = {"ACTIVE", "RESOLVED", "DISMISSED"}
ACTION_STATES = {"MONITOR", "PREPARE", "REROUTE", "BLOCKED"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def loads(value: Any, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except (TypeError, json.JSONDecodeError):
        return default


class RouteIntelligence:
    """Deterministic route, chokepoint, disruption, and exposure analysis."""

    def __init__(self, store, operating_picture):
        self.store = store
        self.operating_picture = operating_picture
        self._init_schema()

    def _init_schema(self) -> None:
        with self.store.db() as connection:
            connection.executescript("""
            CREATE TABLE IF NOT EXISTS route_nodes(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, node_type TEXT NOT NULL,
                name TEXT NOT NULL, country_code TEXT, latitude REAL NOT NULL,
                longitude REAL NOT NULL, infrastructure_id TEXT, status TEXT NOT NULL,
                attributes TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(workspace_id,node_type,name,latitude,longitude)
            );
            CREATE TABLE IF NOT EXISTS route_edges(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, provider TEXT NOT NULL,
                external_id TEXT NOT NULL, from_node_id TEXT NOT NULL,
                to_node_id TEXT NOT NULL, mode TEXT NOT NULL, distance_km REAL NOT NULL,
                transit_hours REAL NOT NULL, base_cost REAL NOT NULL,
                capacity REAL, base_risk REAL NOT NULL, status TEXT NOT NULL,
                bidirectional INTEGER NOT NULL, attributes TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(workspace_id,provider,external_id)
            );
            CREATE TABLE IF NOT EXISTS route_disruptions(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, title TEXT NOT NULL,
                node_id TEXT, edge_id TEXT, probability REAL NOT NULL,
                impact REAL NOT NULL, state TEXT NOT NULL, starts_at TEXT,
                ends_at TEXT, evidence TEXT NOT NULL, source_type TEXT,
                source_id TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(workspace_id,source_type,source_id,node_id,edge_id)
            );
            CREATE TABLE IF NOT EXISTS route_plans(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, name TEXT NOT NULL,
                origin_node_id TEXT NOT NULL, destination_node_id TEXT NOT NULL,
                commodity TEXT NOT NULL, volume REAL, allowed_modes TEXT NOT NULL,
                status TEXT NOT NULL, selected_path TEXT NOT NULL,
                alternatives TEXT NOT NULL, transit_hours REAL,
                estimated_cost REAL, risk_score REAL NOT NULL,
                binding_constraint TEXT NOT NULL, trigger_map TEXT NOT NULL,
                falsifier TEXT NOT NULL, action_state TEXT NOT NULL,
                created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(workspace_id,name,origin_node_id,destination_node_id,commodity)
            );
            CREATE TABLE IF NOT EXISTS route_exposures(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, plan_id TEXT NOT NULL,
                exposure_type TEXT NOT NULL, reference_id TEXT NOT NULL,
                label TEXT NOT NULL, severity REAL NOT NULL,
                evidence TEXT NOT NULL, created_at TEXT NOT NULL,
                UNIQUE(workspace_id,plan_id,exposure_type,reference_id)
            );
            CREATE TABLE IF NOT EXISTS route_revisions(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, plan_id TEXT NOT NULL,
                revision_number INTEGER NOT NULL, action TEXT NOT NULL,
                before_state TEXT NOT NULL, after_state TEXT NOT NULL,
                reason TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL,
                UNIQUE(workspace_id,plan_id,revision_number)
            );
            CREATE INDEX IF NOT EXISTS idx_route_nodes_map
                ON route_nodes(workspace_id,node_type,country_code,status);
            CREATE INDEX IF NOT EXISTS idx_route_edges_graph
                ON route_edges(workspace_id,from_node_id,to_node_id,mode,status);
            CREATE INDEX IF NOT EXISTS idx_route_disruptions_active
                ON route_disruptions(workspace_id,state,node_id,edge_id);
            CREATE INDEX IF NOT EXISTS idx_route_plans_active
                ON route_plans(workspace_id,status,risk_score,updated_at);
            CREATE INDEX IF NOT EXISTS idx_route_exposures_plan
                ON route_exposures(workspace_id,plan_id,exposure_type,severity);
            CREATE INDEX IF NOT EXISTS idx_route_revisions_plan
                ON route_revisions(workspace_id,plan_id,revision_number);
            """)

    def _workspace(self, actor: dict[str, Any]) -> str:
        return str(actor["workspace_id"])

    def _actor(self, actor: dict[str, Any]) -> str:
        return str(actor.get("id") or "system")

    def upsert_node(
        self, actor: dict[str, Any], payload: dict[str, Any]
    ) -> dict[str, Any]:
        node_type = str(payload.get("node_type") or "OTHER").upper()
        name = str(payload.get("name") or "").strip()
        if node_type not in NODE_TYPES:
            raise ValueError("invalid node_type")
        if not name:
            raise ValueError("name required")
        try:
            latitude = float(payload.get("latitude"))
            longitude = float(payload.get("longitude"))
        except (TypeError, ValueError) as exc:
            raise ValueError("latitude and longitude required") from exc
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ValueError("coordinates out of range")
        node_id = stable_id(
            "route-node", self._workspace(actor), node_type, name.lower(),
            f"{latitude:.5f}", f"{longitude:.5f}",
        )
        stamp = now()
        with self.store.db() as connection:
            connection.execute(
                """INSERT INTO route_nodes(
                id,workspace_id,node_type,name,country_code,latitude,longitude,
                infrastructure_id,status,attributes,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(workspace_id,node_type,name,latitude,longitude) DO UPDATE SET
                country_code=excluded.country_code,infrastructure_id=excluded.infrastructure_id,
                status=excluded.status,attributes=excluded.attributes,updated_at=excluded.updated_at""",
                (
                    node_id, self._workspace(actor), node_type, name,
                    payload.get("country_code"), latitude, longitude,
                    payload.get("infrastructure_id"),
                    str(payload.get("status") or "ACTIVE").upper(),
                    json.dumps(payload.get("attributes") or {}, sort_keys=True),
                    stamp, stamp,
                ),
            )
        return self.node(actor, node_id)

    def node(self, actor: dict[str, Any], node_id: str) -> dict[str, Any]:
        with self.store.db() as connection:
            row = connection.execute(
                "SELECT * FROM route_nodes WHERE id=? AND workspace_id=?",
                (node_id, self._workspace(actor)),
            ).fetchone()
        if not row:
            raise KeyError("route node not found")
        item = dict(row)
        item["attributes"] = loads(item["attributes"], {})
        return item

    def upsert_edge(
        self, actor: dict[str, Any], payload: dict[str, Any]
    ) -> dict[str, Any]:
        provider = str(payload.get("provider") or "").strip()
        external_id = str(payload.get("external_id") or "").strip()
        from_id = str(payload.get("from_node_id") or "")
        to_id = str(payload.get("to_node_id") or "")
        mode = str(payload.get("mode") or "").upper()
        if not provider or not external_id:
            raise ValueError("provider and external_id required")
        if mode not in MODES:
            raise ValueError("invalid route mode")
        origin = self.node(actor, from_id)
        destination = self.node(actor, to_id)
        distance = payload.get("distance_km")
        if distance is None:
            distance = haversine_km(
                (origin["latitude"], origin["longitude"]),
                (destination["latitude"], destination["longitude"]),
            )
        distance = float(distance)
        transit = float(payload.get("transit_hours") or 0)
        cost = float(payload.get("base_cost") or 0)
        risk = float(payload.get("base_risk") or 0)
        if distance < 0 or transit <= 0 or cost < 0:
            raise ValueError("invalid route edge metrics")
        if not 0 <= risk <= 1:
            raise ValueError("base_risk must be between 0 and 1")
        status = str(payload.get("status") or "OPEN").upper()
        if status not in EDGE_STATUSES:
            raise ValueError("invalid edge status")
        edge_id = stable_id(
            "route-edge", self._workspace(actor), provider, external_id
        )
        stamp = now()
        with self.store.db() as connection:
            connection.execute(
                """INSERT INTO route_edges(
                id,workspace_id,provider,external_id,from_node_id,to_node_id,
                mode,distance_km,transit_hours,base_cost,capacity,base_risk,status,
                bidirectional,attributes,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(workspace_id,provider,external_id) DO UPDATE SET
                from_node_id=excluded.from_node_id,to_node_id=excluded.to_node_id,
                mode=excluded.mode,distance_km=excluded.distance_km,
                transit_hours=excluded.transit_hours,base_cost=excluded.base_cost,
                capacity=excluded.capacity,base_risk=excluded.base_risk,
                status=excluded.status,bidirectional=excluded.bidirectional,
                attributes=excluded.attributes,updated_at=excluded.updated_at""",
                (
                    edge_id, self._workspace(actor), provider, external_id,
                    from_id, to_id, mode, distance, transit, cost,
                    payload.get("capacity"), risk, status,
                    int(bool(payload.get("bidirectional", True))),
                    json.dumps(payload.get("attributes") or {}, sort_keys=True),
                    stamp, stamp,
                ),
            )
        return self.edge(actor, edge_id)

    def edge(self, actor: dict[str, Any], edge_id: str) -> dict[str, Any]:
        with self.store.db() as connection:
            row = connection.execute(
                "SELECT * FROM route_edges WHERE id=? AND workspace_id=?",
                (edge_id, self._workspace(actor)),
            ).fetchone()
        if not row:
            raise KeyError("route edge not found")
        item = dict(row)
        item["attributes"] = loads(item["attributes"], {})
        item["bidirectional"] = bool(item["bidirectional"])
        return item

    def record_disruption(
        self, actor: dict[str, Any], payload: dict[str, Any]
    ) -> dict[str, Any]:
        node_id = str(payload.get("node_id") or "")
        edge_id = str(payload.get("edge_id") or "")
        if bool(node_id) == bool(edge_id):
            raise ValueError("exactly one node_id or edge_id required")
        if node_id:
            self.node(actor, node_id)
        if edge_id:
            self.edge(actor, edge_id)
        probability = float(payload.get("probability") or 0)
        impact = float(payload.get("impact") or 0)
        if not 0 <= probability <= 1 or not 0 <= impact <= 1:
            raise ValueError("probability and impact must be between 0 and 1")
        state = str(payload.get("state") or "ACTIVE").upper()
        if state not in DISRUPTION_STATES:
            raise ValueError("invalid disruption state")
        source_type = str(payload.get("source_type") or "analyst")
        source_id = str(payload.get("source_id") or "").strip()
        if not source_id:
            raise ValueError("source_id required")
        disruption_id = stable_id(
            "route-disruption", self._workspace(actor), source_type, source_id,
            node_id, edge_id,
        )
        stamp = now()
        with self.store.db() as connection:
            connection.execute(
                """INSERT INTO route_disruptions(
                id,workspace_id,title,node_id,edge_id,probability,impact,state,
                starts_at,ends_at,evidence,source_type,source_id,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(workspace_id,source_type,source_id,node_id,edge_id)
                DO UPDATE SET title=excluded.title,probability=excluded.probability,
                impact=excluded.impact,state=excluded.state,starts_at=excluded.starts_at,
                ends_at=excluded.ends_at,evidence=excluded.evidence,updated_at=excluded.updated_at""",
                (
                    disruption_id, self._workspace(actor),
                    str(payload.get("title") or "Route disruption"),
                    node_id or None, edge_id or None, probability, impact, state,
                    payload.get("starts_at"), payload.get("ends_at"),
                    json.dumps(payload.get("evidence") or {}, sort_keys=True),
                    source_type, source_id, stamp, stamp,
                ),
            )
        return self.disruption(actor, disruption_id)

    def disruption(
        self, actor: dict[str, Any], disruption_id: str
    ) -> dict[str, Any]:
        with self.store.db() as connection:
            row = connection.execute(
                "SELECT * FROM route_disruptions WHERE id=? AND workspace_id=?",
                (disruption_id, self._workspace(actor)),
            ).fetchone()
        if not row:
            raise KeyError("route disruption not found")
        item = dict(row)
        item["evidence"] = loads(item["evidence"], {})
        item["probability"] = float(item["probability"])
        item["impact"] = float(item["impact"])
        return item

    def _active_disruptions(self, actor: dict[str, Any]) -> list[dict[str, Any]]:
        with self.store.db() as connection:
            rows = connection.execute(
                "SELECT * FROM route_disruptions WHERE workspace_id=? AND state='ACTIVE'",
                (self._workspace(actor),),
            ).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["evidence"] = loads(item["evidence"], {})
            output.append(item)
        return output

    def _graph(
        self, actor: dict[str, Any], modes: set[str],
        excluded_edges: set[str] | None = None,
    ) -> dict[str, list[tuple[str, dict[str, Any], bool, float]]]:
        excluded_edges = excluded_edges or set()
        disruptions = self._active_disruptions(actor)
        node_penalty: dict[str, float] = {}
        edge_penalty: dict[str, float] = {}
        for item in disruptions:
            penalty = float(item["probability"]) * float(item["impact"]) * 96
            if item.get("node_id"):
                node_penalty[item["node_id"]] = max(
                    node_penalty.get(item["node_id"], 0), penalty
                )
            if item.get("edge_id"):
                edge_penalty[item["edge_id"]] = max(
                    edge_penalty.get(item["edge_id"], 0), penalty
                )
        with self.store.db() as connection:
            rows = connection.execute(
                "SELECT * FROM route_edges WHERE workspace_id=? AND status!='CLOSED'",
                (self._workspace(actor),),
            ).fetchall()
        graph: dict[str, list[tuple[str, dict[str, Any], bool, float]]] = {}
        for row in rows:
            edge = dict(row)
            if edge["id"] in excluded_edges or (modes and edge["mode"] not in modes):
                continue
            weight = (
                float(edge["transit_hours"])
                + float(edge["base_risk"]) * 48
                + edge_penalty.get(edge["id"], 0)
                + node_penalty.get(edge["to_node_id"], 0)
            )
            graph.setdefault(edge["from_node_id"], []).append(
                (edge["to_node_id"], edge, False, weight)
            )
            if bool(edge["bidirectional"]):
                reverse_weight = weight + node_penalty.get(edge["from_node_id"], 0)
                graph.setdefault(edge["to_node_id"], []).append(
                    (edge["from_node_id"], edge, True, reverse_weight)
                )
        return graph

    def _shortest_path(
        self, actor: dict[str, Any], origin: str, destination: str,
        modes: set[str], excluded_edges: set[str] | None = None,
    ) -> dict[str, Any] | None:
        graph = self._graph(actor, modes, excluded_edges)
        queue: list[tuple[float, str, list[dict[str, Any]]]] = [(0.0, origin, [])]
        best = {origin: 0.0}
        while queue:
            cost, node_id, path = heapq.heappop(queue)
            if node_id == destination:
                return {"weight": cost, "segments": path}
            if cost > best.get(node_id, float("inf")):
                continue
            for next_id, edge, reversed_edge, weight in graph.get(node_id, []):
                candidate = cost + weight
                if candidate >= best.get(next_id, float("inf")):
                    continue
                best[next_id] = candidate
                segment = {
                    "edge_id": edge["id"], "from_node_id": node_id,
                    "to_node_id": next_id, "mode": edge["mode"],
                    "distance_km": float(edge["distance_km"]),
                    "transit_hours": float(edge["transit_hours"]),
                    "base_cost": float(edge["base_cost"]),
                    "base_risk": float(edge["base_risk"]),
                    "reversed": reversed_edge,
                }
                heapq.heappush(queue, (candidate, next_id, path + [segment]))
        return None

    def _route_options(
        self, actor: dict[str, Any], origin: str, destination: str,
        modes: set[str],
    ) -> list[dict[str, Any]]:
        primary = self._shortest_path(actor, origin, destination, modes)
        if not primary:
            return []
        candidates = [primary]
        seen = {tuple(item["edge_id"] for item in primary["segments"])}
        for segment in primary["segments"]:
            alternative = self._shortest_path(
                actor, origin, destination, modes, {segment["edge_id"]}
            )
            if not alternative:
                continue
            key = tuple(item["edge_id"] for item in alternative["segments"])
            if key not in seen:
                seen.add(key)
                candidates.append(alternative)
        candidates.sort(key=lambda item: (item["weight"], len(item["segments"])))
        return candidates[:3]

    def _decorate_route(
        self, actor: dict[str, Any], route: dict[str, Any]
    ) -> dict[str, Any]:
        segments = route["segments"]
        hours = sum(item["transit_hours"] for item in segments)
        cost = sum(item["base_cost"] for item in segments)
        base_risk = (
            sum(item["base_risk"] for item in segments) / max(1, len(segments))
        )
        disruption_map = {
            item["id"]: item for item in self._active_disruptions(actor)
            if item.get("edge_id") in {segment["edge_id"] for segment in segments}
            or item.get("node_id") in {
                node for segment in segments
                for node in (segment["from_node_id"], segment["to_node_id"])
            }
        }
        disruption_risk = sum(
            float(item["probability"]) * float(item["impact"]) * 100
            for item in disruption_map.values()
        )
        risk = min(100.0, base_risk * 50 + disruption_risk)
        output = dict(route)
        output.update({
            "transit_hours": round(hours, 2),
            "estimated_cost": round(cost, 2),
            "risk_score": round(risk, 2),
            "disruption_ids": sorted(disruption_map),
        })
        return output

    def create_plan(
        self, actor: dict[str, Any], payload: dict[str, Any]
    ) -> dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        origin_id = str(payload.get("origin_node_id") or "")
        destination_id = str(payload.get("destination_node_id") or "")
        commodity = str(payload.get("commodity") or "general").strip()
        if not name:
            raise ValueError("name required")
        if origin_id == destination_id:
            raise ValueError("origin and destination must differ")
        self.node(actor, origin_id)
        self.node(actor, destination_id)
        modes = {
            str(value).upper() for value in (payload.get("allowed_modes") or MODES)
        }
        if not modes or not modes.issubset(MODES):
            raise ValueError("invalid allowed_modes")
        plan_id = stable_id(
            "route-plan", self._workspace(actor), name.lower(),
            origin_id, destination_id, commodity.lower(),
        )
        stamp = now()
        with self.store.db() as connection:
            connection.execute(
                """INSERT INTO route_plans(
                id,workspace_id,name,origin_node_id,destination_node_id,commodity,
                volume,allowed_modes,status,selected_path,alternatives,transit_hours,
                estimated_cost,risk_score,binding_constraint,trigger_map,falsifier,
                action_state,created_by,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,'{}','[]',NULL,NULL,0,'','[]','',
                'MONITOR',?,?,?)
                ON CONFLICT(workspace_id,name,origin_node_id,destination_node_id,commodity)
                DO UPDATE SET volume=excluded.volume,allowed_modes=excluded.allowed_modes,
                status=excluded.status,updated_at=excluded.updated_at""",
                (
                    plan_id, self._workspace(actor), name, origin_id, destination_id,
                    commodity, payload.get("volume"),
                    json.dumps(sorted(modes)), str(payload.get("status") or "ACTIVE").upper(),
                    self._actor(actor), stamp, stamp,
                ),
            )
        return self.recalculate(
            actor, plan_id, str(payload.get("reason") or "Route plan created")
        )

    def _revision(
        self, actor: dict[str, Any], plan_id: str, action: str,
        before: Any, after: Any, reason: str,
    ) -> None:
        with self.store.db() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(revision_number),0)+1 FROM route_revisions "
                "WHERE workspace_id=? AND plan_id=?",
                (self._workspace(actor), plan_id),
            ).fetchone()
            number = int(row[0])
            revision_id = stable_id(
                "route-revision", self._workspace(actor), plan_id, str(number)
            )
            connection.execute(
                """INSERT INTO route_revisions(
                id,workspace_id,plan_id,revision_number,action,before_state,
                after_state,reason,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    revision_id, self._workspace(actor), plan_id, number, action,
                    json.dumps(before or {}, sort_keys=True),
                    json.dumps(after or {}, sort_keys=True), reason,
                    self._actor(actor), now(),
                ),
            )

    def recalculate(
        self, actor: dict[str, Any], plan_id: str, reason: str = "Route recalculated"
    ) -> dict[str, Any]:
        before = self.plan(actor, plan_id)
        modes = set(before["allowed_modes"])
        options = [
            self._decorate_route(actor, item)
            for item in self._route_options(
                actor, before["origin_node_id"],
                before["destination_node_id"], modes,
            )
        ]
        if not options:
            selected = {}
            alternatives = []
            risk = 100.0
            action = "BLOCKED"
            constraint = "No open route connects origin and destination"
            triggers = [{"trigger": "An eligible route edge reopens", "state": "BLOCKED"}]
            falsifier = "A complete eligible path becomes available"
            hours = cost = None
        else:
            selected = options[0]
            alternatives = options[1:]
            risk = float(selected["risk_score"])
            action = "REROUTE" if risk >= 70 else "PREPARE" if risk >= 35 else "MONITOR"
            disruptions = [
                self.disruption(actor, value)
                for value in selected["disruption_ids"]
            ]
            if disruptions:
                strongest = max(
                    disruptions,
                    key=lambda item: float(item["probability"]) * float(item["impact"]),
                )
                constraint = (
                    f"{strongest['title']} "
                    f"(p={float(strongest['probability']):.2f}, impact={float(strongest['impact']):.2f})"
                )
            else:
                constraint = "No active disruption is currently binding"
            triggers = [
                {
                    "disruption_id": item["id"], "trigger": item["title"],
                    "probability": float(item["probability"]),
                    "impact": float(item["impact"]),
                }
                for item in disruptions
            ]
            falsifier = (
                "A lower-risk complete route becomes available or the binding "
                "disruption is resolved"
            )
            hours = selected["transit_hours"]
            cost = selected["estimated_cost"]
        with self.store.db() as connection:
            connection.execute(
                """UPDATE route_plans SET selected_path=?,alternatives=?,
                transit_hours=?,estimated_cost=?,risk_score=?,binding_constraint=?,
                trigger_map=?,falsifier=?,action_state=?,updated_at=?
                WHERE id=? AND workspace_id=?""",
                (
                    json.dumps(selected, sort_keys=True),
                    json.dumps(alternatives, sort_keys=True), hours, cost, risk,
                    constraint, json.dumps(triggers, sort_keys=True), falsifier,
                    action, now(), plan_id, self._workspace(actor),
                ),
            )
        after = self.plan(actor, plan_id)
        self._refresh_exposures(actor, after)
        after = self.plan(actor, plan_id)
        material = (
            before.get("selected_path") != after.get("selected_path")
            or float(before.get("risk_score") or 0) != float(after["risk_score"])
            or before.get("action_state") != after.get("action_state")
        )
        if material or not self.revisions(actor, plan_id):
            self._revision(actor, plan_id, "RECALCULATE", before, after, reason)
        return self.plan(actor, plan_id)

    def _refresh_exposures(
        self, actor: dict[str, Any], plan: dict[str, Any]
    ) -> None:
        selected = plan.get("selected_path") or {}
        segments = selected.get("segments") or []
        node_ids = {
            node for segment in segments
            for node in (segment["from_node_id"], segment["to_node_id"])
        }
        edge_ids = {segment["edge_id"] for segment in segments}
        records = []
        for node_id in node_ids:
            node = self.node(actor, node_id)
            if node.get("country_code"):
                records.append((
                    "COUNTRY", node["country_code"], node["country_code"],
                    float(plan["risk_score"]) / 100,
                    {"node_id": node_id},
                ))
            if node["node_type"] == "CHOKEPOINT":
                records.append((
                    "CHOKEPOINT", node_id, node["name"],
                    float(plan["risk_score"]) / 100,
                    {"node_id": node_id},
                ))
        for disruption in self._active_disruptions(actor):
            if disruption.get("node_id") in node_ids or disruption.get("edge_id") in edge_ids:
                records.append((
                    "DISRUPTION", disruption["id"], disruption["title"],
                    float(disruption["probability"]) * float(disruption["impact"]),
                    {"source_type": disruption["source_type"], "source_id": disruption["source_id"]},
                ))
        for segment in segments:
            edge = self.edge(actor, segment["edge_id"])
            for company in edge["attributes"].get("companies") or []:
                records.append((
                    "COMPANY", str(company), str(company),
                    float(plan["risk_score"]) / 100,
                    {"edge_id": edge["id"]},
                ))
        with self.store.db() as connection:
            connection.execute(
                "DELETE FROM route_exposures WHERE workspace_id=? AND plan_id=?",
                (self._workspace(actor), plan["id"]),
            )
            for exposure_type, reference_id, label, severity, evidence in records:
                exposure_id = stable_id(
                    "route-exposure", self._workspace(actor), plan["id"],
                    exposure_type, reference_id,
                )
                connection.execute(
                    """INSERT INTO route_exposures(
                    id,workspace_id,plan_id,exposure_type,reference_id,label,
                    severity,evidence,created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (
                        exposure_id, self._workspace(actor), plan["id"],
                        exposure_type, reference_id, label, severity,
                        json.dumps(evidence, sort_keys=True), now(),
                    ),
                )

    def plan(self, actor: dict[str, Any], plan_id: str) -> dict[str, Any]:
        with self.store.db() as connection:
            row = connection.execute(
                "SELECT * FROM route_plans WHERE id=? AND workspace_id=?",
                (plan_id, self._workspace(actor)),
            ).fetchone()
            exposures = connection.execute(
                "SELECT * FROM route_exposures WHERE workspace_id=? AND plan_id=? "
                "ORDER BY severity DESC,exposure_type,label",
                (self._workspace(actor), plan_id),
            ).fetchall()
        if not row:
            raise KeyError("route plan not found")
        item = dict(row)
        for field, default in (
            ("allowed_modes", []), ("selected_path", {}),
            ("alternatives", []), ("trigger_map", []),
        ):
            item[field] = loads(item[field], default)
        item["risk_score"] = float(item["risk_score"])
        item["exposures"] = []
        for exposure in exposures:
            value = dict(exposure)
            value["severity"] = float(value["severity"])
            value["evidence"] = loads(value["evidence"], {})
            item["exposures"].append(value)
        return item

    def plans(
        self, actor: dict[str, Any], status: str = "ACTIVE", limit: int = 100
    ) -> list[dict[str, Any]]:
        sql = "SELECT id FROM route_plans WHERE workspace_id=?"
        args: list[Any] = [self._workspace(actor)]
        if status:
            sql += " AND status=?"
            args.append(str(status).upper())
        sql += " ORDER BY risk_score DESC,updated_at DESC LIMIT ?"
        args.append(max(1, min(500, int(limit))))
        with self.store.db() as connection:
            rows = connection.execute(sql, args).fetchall()
        return [self.plan(actor, row["id"]) for row in rows]

    def revisions(
        self, actor: dict[str, Any], plan_id: str
    ) -> list[dict[str, Any]]:
        with self.store.db() as connection:
            rows = connection.execute(
                "SELECT * FROM route_revisions WHERE workspace_id=? AND plan_id=? "
                "ORDER BY revision_number",
                (self._workspace(actor), plan_id),
            ).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["before_state"] = loads(item["before_state"], {})
            item["after_state"] = loads(item["after_state"], {})
            output.append(item)
        return output

    def recalculate_active(
        self, actor: dict[str, Any], limit: int = 100
    ) -> dict[str, Any]:
        plans = self.plans(actor, "ACTIVE", limit)
        changed = 0
        for item in plans:
            before = (item["risk_score"], item["action_state"], item["selected_path"])
            after = self.recalculate(actor, item["id"], "Scheduled route reassessment")
            current = (after["risk_score"], after["action_state"], after["selected_path"])
            changed += int(before != current)
        return {"processed": len(plans), "material_changes": changed}

    def import_infrastructure(
        self, actor: dict[str, Any], limit: int = 5000
    ) -> dict[str, Any]:
        mapping = {
            "PORT": "PORT", "AIRPORT": "AIRPORT",
            "BORDER_CROSSING": "BORDER_CROSSING",
            "RAIL_CORRIDOR": "RAIL_TERMINAL",
            "PIPELINE": "PIPELINE_TERMINAL",
            "LNG_TERMINAL": "PIPELINE_TERMINAL",
        }
        created = skipped = 0
        for item in self.operating_picture.infrastructure_list(
            actor, {"limit": limit}
        ):
            node_type = mapping.get(item["infrastructure_type"])
            if not node_type or item.get("latitude") is None:
                skipped += 1
                continue
            self.upsert_node(actor, {
                "node_type": node_type, "name": item["name"],
                "country_code": item.get("country_code"),
                "latitude": item["latitude"], "longitude": item["longitude"],
                "infrastructure_id": item["id"], "status": item["status"],
                "attributes": {"source_url": item.get("source_url")},
            })
            created += 1
        return {"imported": created, "skipped": skipped}

    def geojson(
        self, actor: dict[str, Any], plan_id: str
    ) -> dict[str, Any]:
        plan = self.plan(actor, plan_id)
        features = []
        selected = plan.get("selected_path") or {}
        for segment in selected.get("segments") or []:
            origin = self.node(actor, segment["from_node_id"])
            destination = self.node(actor, segment["to_node_id"])
            features.append({
                "type": "Feature", "id": segment["edge_id"],
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [origin["longitude"], origin["latitude"]],
                        [destination["longitude"], destination["latitude"]],
                    ],
                },
                "properties": {
                    "mode": segment["mode"], "risk": segment["base_risk"],
                    "transit_hours": segment["transit_hours"],
                },
            })
        return {
            "type": "FeatureCollection", "features": features,
            "plan_id": plan_id, "risk_score": plan["risk_score"],
            "action_state": plan["action_state"],
        }

    def scorecard(self, actor: dict[str, Any]) -> dict[str, Any]:
        workspace_id = self._workspace(actor)
        with self.store.db() as connection:
            nodes = connection.execute(
                "SELECT node_type,COUNT(*) total FROM route_nodes "
                "WHERE workspace_id=? GROUP BY node_type", (workspace_id,)
            ).fetchall()
            edges = connection.execute(
                "SELECT mode,COUNT(*) total FROM route_edges "
                "WHERE workspace_id=? GROUP BY mode", (workspace_id,)
            ).fetchall()
            actions = connection.execute(
                "SELECT action_state,COUNT(*) total FROM route_plans "
                "WHERE workspace_id=? GROUP BY action_state", (workspace_id,)
            ).fetchall()
        plans = self.plans(actor, "", 500)
        return {
            "phase": 21,
            "nodes": {row["node_type"]: int(row["total"]) for row in nodes},
            "edges": {row["mode"]: int(row["total"]) for row in edges},
            "plans_by_action": {
                row["action_state"]: int(row["total"]) for row in actions
            },
            "average_risk": round(
                sum(item["risk_score"] for item in plans) / max(1, len(plans)), 2
            ),
            "active_disruptions": len(self._active_disruptions(actor)),
        }
