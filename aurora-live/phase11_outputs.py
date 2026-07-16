from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone

from storage import sid

MODULES = {"AURORA_GRID", "K_ALIGN", "CRF", "IPR", "BLACKGLASS", "COMMAND", "AAIK"}
REVIEW_KINDS = {"adjudication", "red_team"}
REVIEW_DECISIONS = {"affirm", "revise", "reject", "escalate"}


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def loads(value, default):
    try:
        return json.loads(value) if value else default
    except (TypeError, json.JSONDecodeError):
        return default


class SystemOutputStore:
    def __init__(self, store):
        self.store = store
        self.init()

    def init(self):
        with self.store.db() as connection:
            connection.executescript("""
            CREATE TABLE IF NOT EXISTS system_outputs(
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                module TEXT NOT NULL,
                subject_type TEXT NOT NULL,
                subject_id TEXT,
                forecast_id TEXT,
                status TEXT NOT NULL,
                summary TEXT NOT NULL,
                payload TEXT NOT NULL,
                evidence_links TEXT NOT NULL,
                assumptions TEXT NOT NULL,
                constraints TEXT NOT NULL,
                falsifiers TEXT NOT NULL,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS system_output_reviews(
                id TEXT PRIMARY KEY,
                output_id TEXT NOT NULL REFERENCES system_outputs(id) ON DELETE CASCADE,
                workspace_id TEXT NOT NULL,
                review_kind TEXT NOT NULL,
                decision TEXT NOT NULL,
                notes TEXT NOT NULL,
                proposed_changes TEXT NOT NULL,
                reviewer_user_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_system_outputs_workspace_module
                ON system_outputs(workspace_id,module,created_at);
            CREATE INDEX IF NOT EXISTS idx_system_outputs_subject
                ON system_outputs(workspace_id,subject_type,subject_id);
            CREATE INDEX IF NOT EXISTS idx_system_output_reviews_output
                ON system_output_reviews(output_id,created_at);
            """)

    @staticmethod
    def _list(payload, name):
        value = payload.get(name) or []
        if not isinstance(value, list):
            raise ValueError(f"{name} must be a list")
        return value

    def create(self, actor, payload):
        module = str(payload.get("module", "")).strip().upper().replace("-", "_")
        if module not in MODULES:
            raise ValueError("invalid module")
        subject_type = str(payload.get("subject_type", "")).strip().lower()
        if not subject_type:
            raise ValueError("subject_type required")
        summary = str(payload.get("summary", "")).strip()
        if not summary:
            raise ValueError("summary required")
        body = payload.get("payload") or {}
        if not isinstance(body, dict):
            raise ValueError("payload must be an object")
        workspace_id = actor["workspace_id"]
        stamp = now()
        output_id = sid("system-output", workspace_id, module, subject_type, payload.get("subject_id", ""), stamp, secrets.token_hex(4))
        values = (
            output_id,
            workspace_id,
            module,
            subject_type,
            str(payload.get("subject_id", "")).strip() or None,
            str(payload.get("forecast_id", "")).strip() or None,
            str(payload.get("status", "supported")).strip().lower(),
            summary,
            dumps(body),
            dumps(self._list(payload, "evidence_links")),
            dumps(self._list(payload, "assumptions")),
            dumps(self._list(payload, "constraints")),
            dumps(self._list(payload, "falsifiers")),
            actor["id"],
            stamp,
        )
        with self.store.db() as connection:
            connection.execute(
                """INSERT INTO system_outputs(
                id,workspace_id,module,subject_type,subject_id,forecast_id,status,summary,payload,
                evidence_links,assumptions,constraints,falsifiers,created_by,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                values,
            )
        self.store.identity.audit(workspace_id, actor["id"], "system_output.created", "system_output", output_id, metadata={"module": module})
        return self.get(actor, output_id)

    def review(self, actor, output_id, payload):
        self.get(actor, output_id, include_reviews=False)
        kind = str(payload.get("review_kind", "")).strip().lower()
        decision = str(payload.get("decision", "")).strip().lower()
        if kind not in REVIEW_KINDS:
            raise ValueError("invalid review_kind")
        if decision not in REVIEW_DECISIONS:
            raise ValueError("invalid decision")
        proposed = payload.get("proposed_changes") or {}
        if not isinstance(proposed, dict):
            raise ValueError("proposed_changes must be an object")
        stamp = now()
        review_id = sid("system-output-review", output_id, actor["id"], stamp, secrets.token_hex(4))
        with self.store.db() as connection:
            connection.execute(
                """INSERT INTO system_output_reviews(
                id,output_id,workspace_id,review_kind,decision,notes,proposed_changes,reviewer_user_id,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (review_id, output_id, actor["workspace_id"], kind, decision, str(payload.get("notes", "")), dumps(proposed), actor["id"], stamp),
            )
        self.store.identity.audit(actor["workspace_id"], actor["id"], "system_output.reviewed", "system_output", output_id, metadata={"review_id": review_id, "review_kind": kind, "decision": decision})
        return self.get(actor, output_id)

    def get(self, actor, output_id, include_reviews=True):
        with self.store.db() as connection:
            row = connection.execute("SELECT * FROM system_outputs WHERE id=? AND workspace_id=?", (output_id, actor["workspace_id"])).fetchone()
        if not row:
            raise KeyError("system output not found")
        item = dict(row)
        item["payload"] = loads(item["payload"], {})
        for field in ("evidence_links", "assumptions", "constraints", "falsifiers"):
            item[field] = loads(item[field], [])
        if include_reviews:
            with self.store.db() as connection:
                rows = connection.execute("SELECT * FROM system_output_reviews WHERE output_id=? AND workspace_id=? ORDER BY created_at", (output_id, actor["workspace_id"])).fetchall()
            reviews = []
            for row in rows:
                review = dict(row)
                review["proposed_changes"] = loads(review["proposed_changes"], {})
                reviews.append(review)
            item["reviews"] = reviews
        return item

    def list(self, actor, module="", subject_type="", subject_id="", limit=100):
        sql = "SELECT id FROM system_outputs WHERE workspace_id=?"
        args = [actor["workspace_id"]]
        if module:
            module = module.strip().upper().replace("-", "_")
            if module not in MODULES:
                raise ValueError("invalid module")
            sql += " AND module=?"
            args.append(module)
        if subject_type:
            sql += " AND subject_type=?"
            args.append(subject_type.strip().lower())
        if subject_id:
            sql += " AND subject_id=?"
            args.append(subject_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(max(1, min(500, int(limit))))
        with self.store.db() as connection:
            rows = connection.execute(sql, args).fetchall()
        return [self.get(actor, row["id"], include_reviews=False) for row in rows]
