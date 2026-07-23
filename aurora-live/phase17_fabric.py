from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from phase15_mesh import stable_id


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class RealtimeFabric:
    """Durable event journal, checkpointing, replay, and end-to-end observation processing."""

    def __init__(self, store, detection):
        self.store = store
        self.detection = detection
        self._init_schema()

    def _init_schema(self) -> None:
        with self.store.db() as connection:
            connection.executescript("""
            CREATE TABLE IF NOT EXISTS fabric_events(
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT UNIQUE NOT NULL,
                workspace_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                resource_type TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS fabric_checkpoints(
                workspace_id TEXT NOT NULL,
                consumer TEXT NOT NULL,
                last_sequence INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(workspace_id,consumer)
            );
            CREATE INDEX IF NOT EXISTS idx_fabric_events_workspace ON fabric_events(workspace_id,sequence);
            CREATE INDEX IF NOT EXISTS idx_fabric_events_type ON fabric_events(workspace_id,event_type,sequence);
            """)

    def publish(self, actor: dict[str, Any], event_type: str, resource_type: str, resource_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        stamp = now()
        event_id = stable_id("fabric-event", actor["workspace_id"], event_type, resource_type, resource_id, stamp, json.dumps(payload, sort_keys=True))
        with self.store.db() as connection:
            connection.execute(
                "INSERT INTO fabric_events(id,workspace_id,event_type,resource_type,resource_id,payload,created_at) VALUES(?,?,?,?,?,?,?)",
                (event_id, actor["workspace_id"], event_type, resource_type, resource_id, json.dumps(payload, separators=(",", ":"), sort_keys=True), stamp),
            )
            row = connection.execute("SELECT sequence FROM fabric_events WHERE id=?", (event_id,)).fetchone()
        return {"id": event_id, "sequence": int(row["sequence"]), "event_type": event_type, "resource_type": resource_type, "resource_id": resource_id, "payload": payload, "created_at": stamp}

    def stream(self, actor: dict[str, Any], after: int = 0, limit: int = 100, event_type: str = "") -> dict[str, Any]:
        sql = "SELECT * FROM fabric_events WHERE workspace_id=? AND sequence>?"
        args: list[Any] = [actor["workspace_id"], max(0, int(after))]
        if event_type:
            sql += " AND event_type=?"
            args.append(str(event_type))
        sql += " ORDER BY sequence LIMIT ?"
        args.append(max(1, min(1000, int(limit))))
        with self.store.db() as connection:
            rows = connection.execute(sql, args).fetchall()
        events = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item["payload"])
            events.append(item)
        next_cursor = events[-1]["sequence"] if events else max(0, int(after))
        return {"events": events, "next_cursor": next_cursor}

    def checkpoint(self, actor: dict[str, Any], consumer: str, sequence: int) -> dict[str, Any]:
        consumer = str(consumer or "").strip()
        if not consumer:
            raise ValueError("consumer required")
        sequence = max(0, int(sequence))
        stamp = now()
        with self.store.db() as connection:
            connection.execute(
                """INSERT INTO fabric_checkpoints(workspace_id,consumer,last_sequence,updated_at)
                VALUES(?,?,?,?) ON CONFLICT(workspace_id,consumer) DO UPDATE SET
                last_sequence=CASE WHEN excluded.last_sequence>fabric_checkpoints.last_sequence THEN excluded.last_sequence ELSE fabric_checkpoints.last_sequence END,
                updated_at=excluded.updated_at""",
                (actor["workspace_id"], consumer, sequence, stamp),
            )
            row = connection.execute("SELECT * FROM fabric_checkpoints WHERE workspace_id=? AND consumer=?", (actor["workspace_id"], consumer)).fetchone()
        return dict(row)

    def replay(self, actor: dict[str, Any], consumer: str, limit: int = 100) -> dict[str, Any]:
        with self.store.db() as connection:
            row = connection.execute("SELECT last_sequence FROM fabric_checkpoints WHERE workspace_id=? AND consumer=?", (actor["workspace_id"], consumer)).fetchone()
        after = int(row["last_sequence"]) if row else 0
        return self.stream(actor, after=after, limit=limit)

    def process_pending(self, actor: dict[str, Any], limit: int = 100) -> dict[str, Any]:
        workspace_id = actor["workspace_id"]
        with self.store.db() as connection:
            rows = connection.execute(
                """SELECT o.id FROM sensor_observations o LEFT JOIN detection_observations d
                ON d.workspace_id=o.workspace_id AND d.observation_id=o.id
                WHERE o.workspace_id=? AND d.id IS NULL ORDER BY o.observed_at LIMIT ?""",
                (workspace_id, max(1, min(1000, int(limit)))),
            ).fetchall()
        summary = {"processed": 0, "created": 0, "linked": 0, "events_published": 0}
        for row in rows:
            result = self.detection.correlate(actor, row["id"])
            summary["processed"] += 1
            summary["created" if result["outcome"] == "created" else "linked"] += 1
            detection = result["detection"]
            self.publish(actor, "detection.created" if result["outcome"] == "created" else "detection.updated", "detection", detection["id"], {
                "observation_id": row["id"],
                "relation": result["relation"],
                "state": detection["effective_state"],
                "confidence": detection["confidence"],
            })
            summary["events_published"] += 1
        return summary

    def status(self, actor: dict[str, Any]) -> dict[str, Any]:
        workspace_id = actor["workspace_id"]
        with self.store.db() as connection:
            latest = connection.execute("SELECT MAX(sequence) FROM fabric_events WHERE workspace_id=?", (workspace_id,)).fetchone()[0] or 0
            pending = connection.execute(
                """SELECT COUNT(*) FROM sensor_observations o LEFT JOIN detection_observations d
                ON d.workspace_id=o.workspace_id AND d.observation_id=o.id WHERE o.workspace_id=? AND d.id IS NULL""",
                (workspace_id,),
            ).fetchone()[0]
            checkpoints = [dict(row) for row in connection.execute("SELECT * FROM fabric_checkpoints WHERE workspace_id=? ORDER BY consumer", (workspace_id,)).fetchall()]
        return {"phase": 17, "latest_sequence": int(latest), "pending_observations": int(pending), "checkpoints": checkpoints}
