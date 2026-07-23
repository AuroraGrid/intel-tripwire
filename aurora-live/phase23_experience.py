from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from phase15_mesh import stable_id


SUBJECT_TYPES = {
    "DETECTION",
    "ROUTE_PLAN",
    "FORECAST_CANDIDATE",
    "CLAIM",
    "INCIDENT",
    "CASE",
}
ASSIGNMENT_STATES = {"OPEN", "IN_PROGRESS", "DONE", "BLOCKED"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def loads(value: Any, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except (TypeError, json.JSONDecodeError):
        return default


class UnifiedAnalystExperience:
    """Unified read model, saved views, assignments, comments, and activity."""

    def __init__(
        self,
        store,
        mesh,
        integrity,
        detection,
        operating_picture,
        routes,
        autonomous_forecasts,
    ):
        self.store = store
        self.mesh = mesh
        self.integrity = integrity
        self.detection = detection
        self.operating_picture = operating_picture
        self.routes = routes
        self.autonomous_forecasts = autonomous_forecasts
        self._init_schema()

    def _init_schema(self) -> None:
        with self.store.db() as connection:
            connection.executescript("""
            CREATE TABLE IF NOT EXISTS analyst_saved_views(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
                owner_user_id TEXT NOT NULL, name TEXT NOT NULL,
                view_type TEXT NOT NULL, configuration TEXT NOT NULL,
                is_shared INTEGER NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(workspace_id,owner_user_id,name)
            );
            CREATE TABLE IF NOT EXISTS analyst_assignments(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
                subject_type TEXT NOT NULL, subject_id TEXT NOT NULL,
                assignee_user_id TEXT NOT NULL, state TEXT NOT NULL,
                note TEXT NOT NULL, created_by TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(workspace_id,subject_type,subject_id)
            );
            CREATE TABLE IF NOT EXISTS analyst_comments(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
                subject_type TEXT NOT NULL, subject_id TEXT NOT NULL,
                body TEXT NOT NULL, created_by TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS analyst_activity(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
                actor_user_id TEXT NOT NULL, action TEXT NOT NULL,
                subject_type TEXT NOT NULL, subject_id TEXT NOT NULL,
                summary TEXT NOT NULL, metadata TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_saved_views_owner
                ON analyst_saved_views(workspace_id,owner_user_id,updated_at);
            CREATE INDEX IF NOT EXISTS idx_assignments_queue
                ON analyst_assignments(workspace_id,assignee_user_id,state,updated_at);
            CREATE INDEX IF NOT EXISTS idx_comments_subject
                ON analyst_comments(workspace_id,subject_type,subject_id,created_at);
            CREATE INDEX IF NOT EXISTS idx_activity_workspace
                ON analyst_activity(workspace_id,created_at);
            """)

    def _workspace(self, actor: dict[str, Any]) -> str:
        return str(actor["workspace_id"])

    def _actor(self, actor: dict[str, Any]) -> str:
        return str(actor.get("id") or "system")

    def _validate_subject(self, subject_type: str, subject_id: str) -> tuple[str, str]:
        kind = str(subject_type or "").upper()
        identifier = str(subject_id or "").strip()
        if kind not in SUBJECT_TYPES:
            raise ValueError("invalid subject_type")
        if not identifier:
            raise ValueError("subject_id required")
        return kind, identifier

    def _record_activity(
        self,
        actor: dict[str, Any],
        action: str,
        subject_type: str,
        subject_id: str,
        summary: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        stamp = now()
        identifier = stable_id(
            "analyst-activity",
            self._workspace(actor),
            self._actor(actor),
            action,
            subject_type,
            subject_id,
            stamp,
        )
        with self.store.db() as connection:
            connection.execute(
                """INSERT INTO analyst_activity(
                id,workspace_id,actor_user_id,action,subject_type,subject_id,
                summary,metadata,created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    identifier,
                    self._workspace(actor),
                    self._actor(actor),
                    action,
                    subject_type,
                    subject_id,
                    summary[:500],
                    json.dumps(metadata or {}, sort_keys=True),
                    stamp,
                ),
            )

    def overview(self, actor: dict[str, Any]) -> dict[str, Any]:
        detections = self.detection.detections(actor, limit=25)
        routes = self.routes.plans(actor, "ACTIVE", 25)
        candidates = self.autonomous_forecasts.candidates(actor, "", 25)
        contradictions = self.integrity.contradictions(actor, "OPEN", 25)
        coverage = self.mesh.coverage(actor)
        return {
            "phase": 23,
            "generated_at": now(),
            "queues": {
                "detections": len(detections),
                "open_contradictions": len(contradictions),
                "active_routes": len(routes),
                "forecast_candidates": len(candidates),
                "forecast_approvals_pending": sum(
                    item["state"] == "PROPOSED" for item in candidates
                ),
            },
            "priority": {
                "detections": detections[:10],
                "routes": routes[:10],
                "forecasts": candidates[:10],
                "contradictions": contradictions[:10],
            },
            "scorecards": {
                "detections": self.detection.scorecard(actor),
                "evidence_integrity": self.integrity.scorecard(actor),
                "sensor_coverage": coverage,
                "operating_picture": self.operating_picture.scorecard(actor),
                "routes": self.routes.scorecard(actor),
                "forecasts": self.autonomous_forecasts.scorecard(actor),
            },
            "experience": {
                "unified_search": True,
                "saved_views": True,
                "assignments": True,
                "comments": True,
                "shareable_query_state": True,
                "keyboard_navigation": True,
                "mobile_layout": True,
            },
        }

    def search(
        self, actor: dict[str, Any], query: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        needle = str(query or "").strip().lower()
        if len(needle) < 2:
            raise ValueError("query must contain at least two characters")
        limit = max(1, min(200, int(limit)))
        results: list[dict[str, Any]] = []

        def include(kind: str, identifier: str, title: str, subtitle: str, item):
            haystack = json.dumps(item, sort_keys=True, default=str).lower()
            if needle in haystack:
                results.append({
                    "type": kind,
                    "id": identifier,
                    "title": title,
                    "subtitle": subtitle,
                    "updated_at": item.get("updated_at")
                    or item.get("created_at")
                    or "",
                })

        for item in self.detection.detections(actor, limit=200):
            include(
                "DETECTION",
                item["id"],
                item["title"],
                f"{item.get('domain', 'general')} · {item.get('state', 'unknown')}",
                item,
            )
        for item in self.routes.plans(actor, "", 200):
            include(
                "ROUTE_PLAN",
                item["id"],
                item["name"],
                f"{item.get('commodity', 'route')} · {item.get('action_state', 'MONITOR')}",
                item,
            )
        for item in self.autonomous_forecasts.candidates(actor, "", 200):
            include(
                "FORECAST_CANDIDATE",
                item["id"],
                item["question"],
                f"{round(item['probability'] * 100)}% · {item['state']}",
                item,
            )
        for item in self.integrity.claims(actor, "", "", 200):
            include(
                "CLAIM",
                item["id"],
                item["statement"],
                f"{item.get('claim_type', 'FACT')} · {item.get('status', 'unknown')}",
                item,
            )
        return sorted(
            results,
            key=lambda item: (item["updated_at"], item["type"], item["title"]),
            reverse=True,
        )[:limit]

    def save_view(
        self, actor: dict[str, Any], payload: dict[str, Any]
    ) -> dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        view_type = str(payload.get("view_type") or "overview").strip().lower()
        configuration = payload.get("configuration") or {}
        if not name:
            raise ValueError("name required")
        if not isinstance(configuration, dict):
            raise ValueError("configuration must be an object")
        identifier = stable_id(
            "saved-view", self._workspace(actor), self._actor(actor), name.lower()
        )
        stamp = now()
        with self.store.db() as connection:
            connection.execute(
                """INSERT INTO analyst_saved_views(
                id,workspace_id,owner_user_id,name,view_type,configuration,
                is_shared,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(workspace_id,owner_user_id,name) DO UPDATE SET
                view_type=excluded.view_type,
                configuration=excluded.configuration,
                is_shared=excluded.is_shared,
                updated_at=excluded.updated_at""",
                (
                    identifier,
                    self._workspace(actor),
                    self._actor(actor),
                    name,
                    view_type,
                    json.dumps(configuration, sort_keys=True),
                    int(bool(payload.get("is_shared", False))),
                    stamp,
                    stamp,
                ),
            )
        self._record_activity(
            actor, "VIEW_SAVED", "SAVED_VIEW", identifier, f"Saved view: {name}"
        )
        return self.saved_view(actor, identifier)

    def saved_view(
        self, actor: dict[str, Any], view_id: str
    ) -> dict[str, Any]:
        with self.store.db() as connection:
            row = connection.execute(
                """SELECT * FROM analyst_saved_views WHERE id=? AND workspace_id=?
                AND (owner_user_id=? OR is_shared=1)""",
                (view_id, self._workspace(actor), self._actor(actor)),
            ).fetchone()
        if not row:
            raise KeyError("saved view not found")
        item = dict(row)
        item["configuration"] = loads(item["configuration"], {})
        item["is_shared"] = bool(item["is_shared"])
        return item

    def saved_views(self, actor: dict[str, Any]) -> list[dict[str, Any]]:
        with self.store.db() as connection:
            rows = connection.execute(
                """SELECT id FROM analyst_saved_views WHERE workspace_id=?
                AND (owner_user_id=? OR is_shared=1)
                ORDER BY updated_at DESC,name""",
                (self._workspace(actor), self._actor(actor)),
            ).fetchall()
        return [self.saved_view(actor, row["id"]) for row in rows]

    def assign(
        self, actor: dict[str, Any], payload: dict[str, Any]
    ) -> dict[str, Any]:
        kind, subject_id = self._validate_subject(
            payload.get("subject_type"), payload.get("subject_id")
        )
        assignee = str(payload.get("assignee_user_id") or self._actor(actor)).strip()
        state = str(payload.get("state") or "OPEN").upper()
        note = str(payload.get("note") or "").strip()
        if state not in ASSIGNMENT_STATES:
            raise ValueError("invalid assignment state")
        identifier = stable_id(
            "analyst-assignment", self._workspace(actor), kind, subject_id
        )
        stamp = now()
        with self.store.db() as connection:
            connection.execute(
                """INSERT INTO analyst_assignments(
                id,workspace_id,subject_type,subject_id,assignee_user_id,state,
                note,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(workspace_id,subject_type,subject_id) DO UPDATE SET
                assignee_user_id=excluded.assignee_user_id,
                state=excluded.state,note=excluded.note,updated_at=excluded.updated_at""",
                (
                    identifier,
                    self._workspace(actor),
                    kind,
                    subject_id,
                    assignee,
                    state,
                    note,
                    self._actor(actor),
                    stamp,
                    stamp,
                ),
            )
        self.store.identity.audit(
            self._workspace(actor),
            self._actor(actor),
            "analyst.assignment.updated",
            kind.lower(),
            subject_id,
            metadata={"assignee_user_id": assignee, "state": state},
        )
        self._record_activity(
            actor,
            "ASSIGNMENT_UPDATED",
            kind,
            subject_id,
            f"{kind} assigned with state {state}",
            {"assignee_user_id": assignee},
        )
        return self.collaboration(actor, kind, subject_id)["assignment"]

    def comment(
        self, actor: dict[str, Any], payload: dict[str, Any]
    ) -> dict[str, Any]:
        kind, subject_id = self._validate_subject(
            payload.get("subject_type"), payload.get("subject_id")
        )
        body = str(payload.get("body") or "").strip()
        if not body:
            raise ValueError("comment body required")
        stamp = now()
        identifier = stable_id(
            "analyst-comment",
            self._workspace(actor),
            kind,
            subject_id,
            self._actor(actor),
            stamp,
        )
        with self.store.db() as connection:
            connection.execute(
                """INSERT INTO analyst_comments(
                id,workspace_id,subject_type,subject_id,body,created_by,created_at)
                VALUES(?,?,?,?,?,?,?)""",
                (
                    identifier,
                    self._workspace(actor),
                    kind,
                    subject_id,
                    body,
                    self._actor(actor),
                    stamp,
                ),
            )
        self.store.identity.audit(
            self._workspace(actor),
            self._actor(actor),
            "analyst.comment.created",
            kind.lower(),
            subject_id,
            metadata={"comment_id": identifier},
        )
        self._record_activity(
            actor,
            "COMMENT_CREATED",
            kind,
            subject_id,
            f"Comment added to {kind}",
            {"comment_id": identifier},
        )
        return self.comment_record(actor, identifier)

    def comment_record(
        self, actor: dict[str, Any], comment_id: str
    ) -> dict[str, Any]:
        with self.store.db() as connection:
            row = connection.execute(
                "SELECT * FROM analyst_comments WHERE id=? AND workspace_id=?",
                (comment_id, self._workspace(actor)),
            ).fetchone()
        if not row:
            raise KeyError("comment not found")
        return dict(row)

    def collaboration(
        self, actor: dict[str, Any], subject_type: str, subject_id: str
    ) -> dict[str, Any]:
        kind, identifier = self._validate_subject(subject_type, subject_id)
        with self.store.db() as connection:
            assignment = connection.execute(
                """SELECT * FROM analyst_assignments WHERE workspace_id=?
                AND subject_type=? AND subject_id=?""",
                (self._workspace(actor), kind, identifier),
            ).fetchone()
            comments = connection.execute(
                """SELECT * FROM analyst_comments WHERE workspace_id=?
                AND subject_type=? AND subject_id=? ORDER BY created_at""",
                (self._workspace(actor), kind, identifier),
            ).fetchall()
        return {
            "subject_type": kind,
            "subject_id": identifier,
            "assignment": dict(assignment) if assignment else None,
            "comments": [dict(row) for row in comments],
        }

    def activity(
        self, actor: dict[str, Any], limit: int = 100
    ) -> list[dict[str, Any]]:
        with self.store.db() as connection:
            rows = connection.execute(
                """SELECT * FROM analyst_activity WHERE workspace_id=?
                ORDER BY created_at DESC LIMIT ?""",
                (self._workspace(actor), max(1, min(500, int(limit)))),
            ).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["metadata"] = loads(item["metadata"], {})
            output.append(item)
        return output
