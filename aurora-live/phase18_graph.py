from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from phase15_mesh import stable_id

ENTITY_TYPES = {"PERSON", "ORGANIZATION", "GOVERNMENT", "ARMED_GROUP", "VESSEL", "AIRCRAFT", "PORT", "AIRPORT", "INFRASTRUCTURE", "LOCATION", "EVENT", "OTHER"}
RELATION_TYPES = {"OPERATES", "CONTROLS", "ATTACKED", "OCCURRED_AT", "INVOLVED", "DISRUPTED", "REFERENCES", "REPRESENTS", "SUPPORTS", "RELATED_TO"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def norm(value: Any) -> str:
    text = re.sub(r"[^a-z0-9\s-]", " ", str(value or "").lower())
    return " ".join(text.split())


def loads(value: Any, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except (TypeError, json.JSONDecodeError):
        return default


class EntityGraph:
    """Workspace-scoped canonical entities, aliases, temporal relations, provenance, and replay."""

    CONSUMER = "phase18.entity-graph"

    def __init__(self, store, detection, fabric):
        self.store = store
        self.detection = detection
        self.fabric = fabric
        self._init_schema()

    def _init_schema(self) -> None:
        with self.store.db() as connection:
            connection.executescript("""
            CREATE TABLE IF NOT EXISTS graph_entities(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, entity_type TEXT NOT NULL,
                canonical_name TEXT NOT NULL, normalized_name TEXT NOT NULL, status TEXT NOT NULL,
                attributes TEXT NOT NULL, merged_into TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(workspace_id,entity_type,normalized_name)
            );
            CREATE TABLE IF NOT EXISTS graph_aliases(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, entity_id TEXT NOT NULL,
                alias TEXT NOT NULL, normalized_alias TEXT NOT NULL, language TEXT,
                source_type TEXT, source_id TEXT, confidence REAL NOT NULL, created_at TEXT NOT NULL,
                UNIQUE(workspace_id,entity_id,normalized_alias)
            );
            CREATE TABLE IF NOT EXISTS graph_relations(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, subject_id TEXT NOT NULL,
                relation_type TEXT NOT NULL, object_id TEXT NOT NULL, confidence REAL NOT NULL,
                valid_from TEXT, valid_to TEXT, provenance_type TEXT, provenance_id TEXT,
                attributes TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(workspace_id,subject_id,relation_type,object_id,provenance_type,provenance_id)
            );
            CREATE TABLE IF NOT EXISTS graph_revisions(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, resource_type TEXT NOT NULL,
                resource_id TEXT NOT NULL, action TEXT NOT NULL, before_state TEXT NOT NULL,
                after_state TEXT NOT NULL, reason TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_graph_entities_workspace ON graph_entities(workspace_id,entity_type,status,normalized_name);
            CREATE INDEX IF NOT EXISTS idx_graph_aliases_lookup ON graph_aliases(workspace_id,normalized_alias,confidence);
            CREATE INDEX IF NOT EXISTS idx_graph_relations_subject ON graph_relations(workspace_id,subject_id,relation_type,status);
            CREATE INDEX IF NOT EXISTS idx_graph_relations_object ON graph_relations(workspace_id,object_id,relation_type,status);
            CREATE INDEX IF NOT EXISTS idx_graph_revisions_resource ON graph_revisions(workspace_id,resource_type,resource_id,created_at);
            """)

    def _workspace(self, actor: dict[str, Any]) -> str:
        return str(actor["workspace_id"])

    def _actor(self, actor: dict[str, Any]) -> str:
        return str(actor.get("id") or "system")

    def _revision(self, actor: dict[str, Any], resource_type: str, resource_id: str, action: str, before: Any, after: Any, reason: str) -> None:
        stamp = now()
        revision_id = stable_id("graph-revision", self._workspace(actor), resource_type, resource_id, action, stamp)
        with self.store.db() as connection:
            connection.execute(
                "INSERT INTO graph_revisions(id,workspace_id,resource_type,resource_id,action,before_state,after_state,reason,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (revision_id, self._workspace(actor), resource_type, resource_id, action, json.dumps(before or {}, sort_keys=True), json.dumps(after or {}, sort_keys=True), str(reason), self._actor(actor), stamp),
            )

    def resolve(self, actor: dict[str, Any], name: str, entity_type: str = "OTHER") -> dict[str, Any] | None:
        entity_type = str(entity_type or "OTHER").upper()
        needle = norm(name)
        if not needle:
            return None
        with self.store.db() as connection:
            row = connection.execute(
                "SELECT id FROM graph_entities WHERE workspace_id=? AND entity_type=? AND normalized_name=? AND status='ACTIVE'",
                (self._workspace(actor), entity_type, needle),
            ).fetchone()
            if not row:
                row = connection.execute(
                    """SELECT e.id FROM graph_aliases a JOIN graph_entities e ON e.id=a.entity_id
                    WHERE a.workspace_id=? AND a.normalized_alias=? AND e.status='ACTIVE'
                    ORDER BY a.confidence DESC LIMIT 1""",
                    (self._workspace(actor), needle),
                ).fetchone()
        return self.entity(actor, row["id"]) if row else None

    def upsert_entity(self, actor: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("canonical_name") or payload.get("name") or "").strip()
        entity_type = str(payload.get("entity_type") or "OTHER").strip().upper()
        if not name:
            raise ValueError("canonical_name required")
        if entity_type not in ENTITY_TYPES:
            raise ValueError("invalid entity_type")
        normalized = norm(name)
        attributes = payload.get("attributes") or {}
        stamp = now()
        entity_id = stable_id("graph-entity", self._workspace(actor), entity_type, normalized)
        existing = self.resolve(actor, name, entity_type)
        before = existing or {}
        with self.store.db() as connection:
            connection.execute(
                """INSERT INTO graph_entities(id,workspace_id,entity_type,canonical_name,normalized_name,status,attributes,merged_into,created_at,updated_at)
                VALUES(?,?,?,?,?,'ACTIVE',?,NULL,?,?) ON CONFLICT(workspace_id,entity_type,normalized_name) DO UPDATE SET
                canonical_name=excluded.canonical_name,attributes=excluded.attributes,updated_at=excluded.updated_at""",
                (entity_id, self._workspace(actor), entity_type, name, normalized, json.dumps(attributes, separators=(",", ":"), sort_keys=True), stamp, stamp),
            )
        item = self.entity(actor, entity_id)
        self._revision(actor, "entity", entity_id, "UPSERT", before, item, str(payload.get("reason") or "Entity upsert"))
        self.store.identity.audit(self._workspace(actor), self._actor(actor), "graph.entity.upserted", "entity", entity_id, metadata={"entity_type": entity_type})
        for alias in payload.get("aliases") or []:
            self.add_alias(actor, entity_id, alias if isinstance(alias, dict) else {"alias": alias})
        return self.entity(actor, entity_id)

    def add_alias(self, actor: dict[str, Any], entity_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.entity(actor, entity_id)
        alias = str(payload.get("alias") or "").strip()
        if not alias:
            raise ValueError("alias required")
        normalized = norm(alias)
        confidence = float(payload.get("confidence", 1.0))
        if not 0 <= confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        alias_id = stable_id("graph-alias", self._workspace(actor), entity_id, normalized)
        stamp = now()
        with self.store.db() as connection:
            conflict = connection.execute(
                """SELECT a.entity_id FROM graph_aliases a JOIN graph_entities e ON e.id=a.entity_id
                WHERE a.workspace_id=? AND a.normalized_alias=? AND a.entity_id!=? AND e.status='ACTIVE' ORDER BY a.confidence DESC LIMIT 1""",
                (self._workspace(actor), normalized, entity_id),
            ).fetchone()
            if conflict and confidence >= 0.8:
                raise ValueError("alias already resolves to another active entity")
            connection.execute(
                """INSERT INTO graph_aliases(id,workspace_id,entity_id,alias,normalized_alias,language,source_type,source_id,confidence,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(workspace_id,entity_id,normalized_alias) DO UPDATE SET confidence=excluded.confidence,source_type=excluded.source_type,source_id=excluded.source_id""",
                (alias_id, self._workspace(actor), entity_id, alias, normalized, payload.get("language"), payload.get("source_type"), payload.get("source_id"), confidence, stamp),
            )
        return next(item for item in self.entity(actor, entity_id)["aliases"] if item["id"] == alias_id)

    def relate(self, actor: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        subject_id = str(payload.get("subject_id") or "")
        object_id = str(payload.get("object_id") or "")
        relation_type = str(payload.get("relation_type") or "RELATED_TO").upper()
        self.entity(actor, subject_id)
        self.entity(actor, object_id)
        if relation_type not in RELATION_TYPES:
            raise ValueError("invalid relation_type")
        confidence = float(payload.get("confidence", 0.5))
        if not 0 <= confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        provenance_type = str(payload.get("provenance_type") or "analyst")
        provenance_id = str(payload.get("provenance_id") or "")
        relation_id = stable_id("graph-relation", self._workspace(actor), subject_id, relation_type, object_id, provenance_type, provenance_id)
        stamp = now()
        with self.store.db() as connection:
            connection.execute(
                """INSERT INTO graph_relations(id,workspace_id,subject_id,relation_type,object_id,confidence,valid_from,valid_to,provenance_type,provenance_id,attributes,status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,'ACTIVE',?,?) ON CONFLICT(workspace_id,subject_id,relation_type,object_id,provenance_type,provenance_id) DO UPDATE SET
                confidence=excluded.confidence,valid_from=excluded.valid_from,valid_to=excluded.valid_to,attributes=excluded.attributes,status='ACTIVE',updated_at=excluded.updated_at""",
                (relation_id, self._workspace(actor), subject_id, relation_type, object_id, confidence, payload.get("valid_from"), payload.get("valid_to"), provenance_type, provenance_id, json.dumps(payload.get("attributes") or {}, sort_keys=True), stamp, stamp),
            )
        item = self.relation(actor, relation_id)
        self._revision(actor, "relation", relation_id, "UPSERT", {}, item, str(payload.get("reason") or "Relationship upsert"))
        return item

    def merge(self, actor: dict[str, Any], source_id: str, target_id: str, reason: str) -> dict[str, Any]:
        if source_id == target_id:
            raise ValueError("source and target must differ")
        source = self.entity(actor, source_id)
        target = self.entity(actor, target_id)
        if source["entity_type"] != target["entity_type"]:
            raise ValueError("entity types must match")
        stamp = now()
        with self.store.db() as connection:
            connection.execute("UPDATE graph_entities SET status='MERGED',merged_into=?,updated_at=? WHERE id=? AND workspace_id=?", (target_id, stamp, source_id, self._workspace(actor)))
            connection.execute("UPDATE graph_aliases SET entity_id=? WHERE workspace_id=? AND entity_id=? AND normalized_alias NOT IN (SELECT normalized_alias FROM graph_aliases WHERE workspace_id=? AND entity_id=?)", (target_id, self._workspace(actor), source_id, self._workspace(actor), target_id))
            connection.execute("UPDATE graph_relations SET subject_id=?,updated_at=? WHERE workspace_id=? AND subject_id=?", (target_id, stamp, self._workspace(actor), source_id))
            connection.execute("UPDATE graph_relations SET object_id=?,updated_at=? WHERE workspace_id=? AND object_id=?", (target_id, stamp, self._workspace(actor), source_id))
        after = self.entity(actor, source_id, include_inactive=True)
        self._revision(actor, "entity", source_id, "MERGE", source, after, reason or "Entity merge")
        return {"source": after, "target": self.entity(actor, target_id)}

    def split(self, actor: dict[str, Any], source_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        source = self.entity(actor, source_id, include_inactive=True)
        if source["status"] != "MERGED" or not source.get("merged_into"):
            raise ValueError("entity is not merged")
        stamp = now()
        with self.store.db() as connection:
            connection.execute("UPDATE graph_entities SET status='ACTIVE',merged_into=NULL,updated_at=? WHERE id=? AND workspace_id=?", (stamp, source_id, self._workspace(actor)))
        restored = self.entity(actor, source_id)
        self._revision(actor, "entity", source_id, "SPLIT", source, restored, str(payload.get("reason") or "Entity split"))
        return restored

    def entity(self, actor: dict[str, Any], entity_id: str, include_inactive: bool = False) -> dict[str, Any]:
        sql = "SELECT * FROM graph_entities WHERE id=? AND workspace_id=?"
        args = [entity_id, self._workspace(actor)]
        if not include_inactive:
            sql += " AND status='ACTIVE'"
        with self.store.db() as connection:
            row = connection.execute(sql, args).fetchone()
            aliases = connection.execute("SELECT * FROM graph_aliases WHERE workspace_id=? AND entity_id=? ORDER BY confidence DESC,alias", (self._workspace(actor), entity_id)).fetchall()
        if not row:
            raise KeyError("entity not found")
        item = dict(row)
        item["attributes"] = loads(item.get("attributes"), {})
        item["aliases"] = [dict(alias) for alias in aliases]
        return item

    def relation(self, actor: dict[str, Any], relation_id: str) -> dict[str, Any]:
        with self.store.db() as connection:
            row = connection.execute("SELECT * FROM graph_relations WHERE id=? AND workspace_id=?", (relation_id, self._workspace(actor))).fetchone()
        if not row:
            raise KeyError("relation not found")
        item = dict(row)
        item["attributes"] = loads(item.get("attributes"), {})
        item["confidence"] = float(item["confidence"])
        return item

    def graph(self, actor: dict[str, Any], entity_id: str = "", limit: int = 500) -> dict[str, Any]:
        workspace_id = self._workspace(actor)
        with self.store.db() as connection:
            if entity_id:
                self.entity(actor, entity_id, include_inactive=True)
                relations = connection.execute("SELECT * FROM graph_relations WHERE workspace_id=? AND status='ACTIVE' AND (subject_id=? OR object_id=?) ORDER BY updated_at DESC LIMIT ?", (workspace_id, entity_id, entity_id, max(1, min(2000, int(limit))))).fetchall()
                ids = {entity_id} | {row["subject_id"] for row in relations} | {row["object_id"] for row in relations}
                entities = [connection.execute("SELECT * FROM graph_entities WHERE workspace_id=? AND id=?", (workspace_id, value)).fetchone() for value in ids]
            else:
                entities = connection.execute("SELECT * FROM graph_entities WHERE workspace_id=? AND status='ACTIVE' ORDER BY updated_at DESC LIMIT ?", (workspace_id, max(1, min(2000, int(limit))))).fetchall()
                ids = {row["id"] for row in entities}
                relations = connection.execute("SELECT * FROM graph_relations WHERE workspace_id=? AND status='ACTIVE' ORDER BY updated_at DESC LIMIT ?", (workspace_id, max(1, min(5000, int(limit) * 4)))).fetchall()
                relations = [row for row in relations if row["subject_id"] in ids and row["object_id"] in ids]
        nodes = [{"id": row["id"], "type": row["entity_type"], "label": row["canonical_name"], "status": row["status"]} for row in entities if row]
        edges = [{"id": row["id"], "from": row["subject_id"], "to": row["object_id"], "type": row["relation_type"], "confidence": float(row["confidence"]), "valid_from": row["valid_from"], "valid_to": row["valid_to"], "provenance_type": row["provenance_type"], "provenance_id": row["provenance_id"]} for row in relations]
        return {"nodes": nodes, "edges": edges, "node_count": len(nodes), "edge_count": len(edges)}

    def _detection_event(self, actor: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
        detection = self.detection.detection(actor, event["resource_id"])
        event_entity = self.upsert_entity(actor, {"canonical_name": detection["title"], "entity_type": "EVENT", "attributes": {"detection_id": detection["id"], "domain": detection["domain"], "severity": detection["severity"], "state": detection["effective_state"], "confidence": detection["confidence"]}, "aliases": [], "reason": "Detection materialized as graph event"})
        linked = 0
        for observation in detection.get("observations") or []:
            payload = observation.get("payload") or {}
            location_name = str(payload.get("location_name") or payload.get("country") or "").strip()
            if location_name:
                location = self.upsert_entity(actor, {"canonical_name": location_name, "entity_type": "LOCATION", "reason": "Detection location extraction"})
                self.relate(actor, {"subject_id": event_entity["id"], "relation_type": "OCCURRED_AT", "object_id": location["id"], "confidence": 0.8, "provenance_type": "observation", "provenance_id": observation["observation_id"]})
                linked += 1
            for raw in payload.get("entities") or []:
                if not isinstance(raw, dict) or not raw.get("name"):
                    continue
                entity = self.upsert_entity(actor, {"canonical_name": raw["name"], "entity_type": raw.get("type", "OTHER"), "aliases": raw.get("aliases") or [], "attributes": raw.get("attributes") or {}, "reason": "Observation entity extraction"})
                self.relate(actor, {"subject_id": event_entity["id"], "relation_type": "INVOLVED", "object_id": entity["id"], "confidence": float(raw.get("confidence", 0.7)), "provenance_type": "observation", "provenance_id": observation["observation_id"]})
                linked += 1
        return {"event_entity_id": event_entity["id"], "relations_created": linked}

    def process_fabric(self, actor: dict[str, Any], limit: int = 100) -> dict[str, Any]:
        replay = self.fabric.replay(actor, self.CONSUMER, limit)
        processed = materialized = 0
        last_sequence = None
        for event in replay["events"]:
            last_sequence = int(event["sequence"])
            processed += 1
            if event["event_type"] in {"detection.created", "detection.updated"} and event["resource_type"] == "detection":
                self._detection_event(actor, event)
                materialized += 1
        if last_sequence is not None:
            self.fabric.checkpoint(actor, self.CONSUMER, last_sequence)
        return {"processed": processed, "materialized": materialized, "next_cursor": replay["next_cursor"]}

    def revisions(self, actor: dict[str, Any], resource_type: str, resource_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self.store.db() as connection:
            rows = connection.execute("SELECT * FROM graph_revisions WHERE workspace_id=? AND resource_type=? AND resource_id=? ORDER BY created_at DESC LIMIT ?", (self._workspace(actor), resource_type, resource_id, max(1, min(500, int(limit))))).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["before_state"] = loads(item["before_state"], {})
            item["after_state"] = loads(item["after_state"], {})
            output.append(item)
        return output

    def scorecard(self, actor: dict[str, Any]) -> dict[str, Any]:
        workspace_id = self._workspace(actor)
        with self.store.db() as connection:
            entities = connection.execute("SELECT entity_type,COUNT(*) total FROM graph_entities WHERE workspace_id=? AND status='ACTIVE' GROUP BY entity_type", (workspace_id,)).fetchall()
            relations = connection.execute("SELECT relation_type,COUNT(*) total FROM graph_relations WHERE workspace_id=? AND status='ACTIVE' GROUP BY relation_type", (workspace_id,)).fetchall()
            aliases = connection.execute("SELECT COUNT(*) FROM graph_aliases WHERE workspace_id=?", (workspace_id,)).fetchone()[0]
        return {"phase": 18, "entities_by_type": {row["entity_type"]: int(row["total"]) for row in entities}, "relations_by_type": {row["relation_type"]: int(row["total"]) for row in relations}, "aliases": int(aliases)}
