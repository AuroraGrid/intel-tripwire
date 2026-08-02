from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

from database import Database, DatabaseIntegrityError


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sid(*values):
    return hashlib.sha256("|".join(map(str, values)).encode()).hexdigest()[:24]


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def loads(value, default):
    try:
        return json.loads(value) if value else default
    except (json.JSONDecodeError, TypeError):
        return default


class Store:
    def __init__(self, path="aurora-live.db", database_url=None):
        self.database = Database(database_url or os.getenv("DATABASE_URL") or str(path))
        self.backend = self.database.backend
        self.path = self.database.path
        self.init()
        from identity import Identity
        self.identity = Identity(self)
        self._migrate_workspace_columns()

    def db(self):
        return self.database.connection()

    def init(self):
        timeline_id = "INTEGER PRIMARY KEY AUTOINCREMENT" if self.backend == "sqlite" else "BIGSERIAL PRIMARY KEY"
        with self.db() as connection:
            connection.executescript(f"""
            CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,email TEXT UNIQUE,token_hash TEXT UNIQUE,role TEXT,created_at TEXT);
            CREATE TABLE IF NOT EXISTS watchlists(id TEXT PRIMARY KEY,user_id TEXT REFERENCES users(id) ON DELETE CASCADE,name TEXT,query TEXT,categories TEXT,severities TEXT,min_confidence INTEGER,created_at TEXT);
            CREATE TABLE IF NOT EXISTS incidents(id TEXT PRIMARY KEY,title TEXT,category TEXT,severity TEXT,status TEXT,grade TEXT,confidence INTEGER,action TEXT,first_seen TEXT,last_seen TEXT,payload TEXT);
            CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY,incident_id TEXT REFERENCES incidents(id) ON DELETE CASCADE,source_family TEXT,title TEXT,url TEXT,official INTEGER,published_at TEXT,payload TEXT);
            CREATE TABLE IF NOT EXISTS timeline(id {timeline_id},incident_id TEXT REFERENCES incidents(id) ON DELETE CASCADE,event_type TEXT,summary TEXT,created_at TEXT,payload TEXT);
            CREATE TABLE IF NOT EXISTS alerts(id TEXT PRIMARY KEY,user_id TEXT,watchlist_id TEXT,incident_id TEXT,created_at TEXT,UNIQUE(watchlist_id,incident_id));
            CREATE TABLE IF NOT EXISTS notes(id TEXT PRIMARY KEY,incident_id TEXT,user_id TEXT,body TEXT,created_at TEXT);
            """)

    def _column(self, table, name):
        if name in self.database.column_names(table):
            return
        with self.db() as connection:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} TEXT")

    def _migrate_workspace_columns(self):
        for table in ("watchlists", "incidents", "evidence", "timeline", "alerts", "notes"):
            self._column(table, "workspace_id")
        default = self.identity.default_workspace
        with self.db() as connection:
            for table in ("watchlists", "incidents", "evidence", "timeline", "alerts", "notes"):
                connection.execute(f"UPDATE {table} SET workspace_id=? WHERE workspace_id IS NULL", (default,))
            connection.executescript("""
            CREATE INDEX IF NOT EXISTS idx_watchlists_workspace ON watchlists(workspace_id,user_id,created_at);
            CREATE INDEX IF NOT EXISTS idx_incidents_workspace ON incidents(workspace_id,last_seen);
            CREATE INDEX IF NOT EXISTS idx_alerts_workspace ON alerts(workspace_id,user_id,created_at);
            CREATE INDEX IF NOT EXISTS idx_notes_workspace ON notes(workspace_id,incident_id);
            """)

    def workspace_id(self, user_id=None, workspace_id=None):
        return workspace_id or self.identity.workspace_id(user_id)

    def create_user(self, email, role="analyst"):
        email = email.strip().lower()
        if "@" not in email:
            raise ValueError("valid email required")
        if role not in {"viewer", "analyst", "admin"}:
            raise ValueError("invalid role")
        first = self.users() == 0
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode()).hexdigest()
        user_id = sid("user", email)
        created = now()
        with self.db() as connection:
            connection.execute(
                "INSERT INTO users(id,email,token_hash,role,created_at) VALUES(?,?,?,?,?)",
                (user_id, email, digest, role, created),
            )
        workspace_role = "owner" if first and role == "admin" else role
        workspace_id = self.identity.register(user_id, workspace_role, digest, created)
        self.identity.audit(workspace_id, user_id, "user.created", "user", user_id, metadata={"role": workspace_role})
        return {"id": user_id, "email": email, "role": role, "workspace_id": workspace_id, "created_at": created}, token

    def auth(self, token):
        return self.identity.auth(token)

    @staticmethod
    def open_access_enabled() -> bool:
        import os

        return str(os.getenv("AURORA_OPEN_ACCESS") or "").strip().lower() in {"1", "true", "yes", "on"}

    def first_workspace_user(self):
        with self.db() as connection:
            row = connection.execute(
                """SELECT u.id,u.email,u.role,m.workspace_id,m.role workspace_role
                FROM users u JOIN memberships m ON m.user_id=u.id
                ORDER BY u.created_at ASC LIMIT 1"""
            ).fetchone()
        return dict(row) if row else None

    def ensure_open_access_user(self):
        """On a fresh free-host DB there are zero users — create a default owner."""
        user = self.first_workspace_user()
        if user:
            return user
        created, _token = self.create_user("open-beta@aurora.local", "admin")
        with self.db() as connection:
            row = connection.execute(
                """SELECT u.id,u.email,u.role,m.workspace_id,m.role workspace_role
                FROM users u JOIN memberships m ON m.user_id=u.id
                WHERE u.id=? LIMIT 1""",
                (created["id"],),
            ).fetchone()
        return dict(row) if row else {
            "id": created["id"],
            "email": created["email"],
            "role": created["role"],
            "workspace_id": created["workspace_id"],
            "workspace_role": "owner",
        }

    def issue_open_session(self, name: str = "open-access"):
        """Issue a session token for the first workspace user (open beta mode)."""
        user = self.ensure_open_access_user() if self.open_access_enabled() else self.first_workspace_user()
        if not user:
            return None
        token = self.identity.issue_session_secret(user["id"], user["workspace_id"], name=name)
        return {
            "token": token,
            "user": {
                "id": user["id"],
                "email": user["email"],
                "role": "admin" if user.get("workspace_role") == "owner" else user.get("role"),
                "workspace_id": user["workspace_id"],
            },
            "open_access": True,
        }

    def login_with_password(self, password: str):
        """Password login, or free open-access session when AURORA_OPEN_ACCESS=1."""
        import os
        import secrets as _secrets

        if self.open_access_enabled():
            return self.issue_open_session(name="open-access")
        expected = str(os.getenv("AURORA_FRIEND_PASSWORD") or "")
        supplied = str(password or "")
        if not expected or not supplied or not _secrets.compare_digest(supplied, expected):
            return None
        user = self.first_workspace_user()
        if not user:
            return None
        token = self.identity.issue_session_secret(user["id"], user["workspace_id"], name="friend-password")
        return {
            "token": token,
            "user": {
                "id": user["id"],
                "email": user["email"],
                "role": "admin" if user.get("workspace_role") == "owner" else user.get("role"),
                "workspace_id": user["workspace_id"],
            },
            "open_access": False,
        }

    def users(self):
        with self.db() as connection:
            return connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def add_watchlist(self, user_id, payload, workspace_id=None):
        workspace_id = self.workspace_id(user_id, workspace_id)
        name = str(payload.get("name", "")).strip()
        query = str(payload.get("query", "")).strip()
        confidence = max(0, min(100, int(payload.get("min_confidence", 0))))
        if not name:
            raise ValueError("name required")
        categories = sorted(set(payload.get("categories") or []))
        severities = sorted(set(payload.get("severities") or []))
        watchlist_id = sid("watch", workspace_id, user_id, name, now(), secrets.token_hex(4))
        created = now()
        with self.db() as connection:
            connection.execute(
                "INSERT INTO watchlists(id,user_id,name,query,categories,severities,min_confidence,created_at,workspace_id) VALUES(?,?,?,?,?,?,?,?,?)",
                (watchlist_id, user_id, name, query, dumps(categories), dumps(severities), confidence, created, workspace_id),
            )
        self.identity.audit(workspace_id, user_id, "watchlist.created", "watchlist", watchlist_id)
        return next(item for item in self.watchlists(user_id, workspace_id) if item["id"] == watchlist_id)

    def watchlists(self, user_id, workspace_id=None):
        workspace_id = self.workspace_id(user_id, workspace_id)
        with self.db() as connection:
            rows = connection.execute(
                "SELECT * FROM watchlists WHERE user_id=? AND workspace_id=? ORDER BY created_at DESC",
                (user_id, workspace_id),
            ).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["categories"] = loads(item.pop("categories"), [])
            item["severities"] = loads(item.pop("severities"), [])
            output.append(item)
        return output

    def delete_watchlist(self, user_id, watchlist_id, workspace_id=None):
        workspace_id = self.workspace_id(user_id, workspace_id)
        with self.db() as connection:
            changed = connection.execute(
                "DELETE FROM watchlists WHERE user_id=? AND workspace_id=? AND id=?",
                (user_id, workspace_id, watchlist_id),
            ).rowcount
        if changed:
            self.identity.audit(workspace_id, user_id, "watchlist.deleted", "watchlist", watchlist_id)
        return changed > 0

    def ingest(self, payload, workspace_id=None, actor_user_id=None):
        workspace_id = self.workspace_id(actor_user_id, workspace_id)
        created = updated = 0
        incident_ids = []
        for event in payload.get("events", []):
            external_id = str(event.get("id") or event.get("claim_id") or sid(event.get("title"), event.get("published_at")))
            incident_id = sid("incident", workspace_id, external_id)
            incident_ids.append(incident_id)
            enriched = dict(event)
            enriched.setdefault("source_id", external_id)
            with self.db() as connection:
                old = connection.execute(
                    "SELECT payload,first_seen FROM incidents WHERE id=? AND workspace_id=?",
                    (incident_id, workspace_id),
                ).fetchone()
            changed = bool(old and any(loads(old["payload"], {}).get(key) != event.get(key) for key in ("title", "severity", "k_align_status", "confidence_score", "action_state")))
            first_seen = old["first_seen"] if old else now()
            seen = now()
            with self.db() as connection:
                connection.execute(
                    """INSERT INTO incidents(id,title,category,severity,status,grade,confidence,action,first_seen,last_seen,payload,workspace_id)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,category=excluded.category,
                    severity=excluded.severity,status=excluded.status,grade=excluded.grade,confidence=excluded.confidence,
                    action=excluded.action,last_seen=excluded.last_seen,payload=excluded.payload,workspace_id=excluded.workspace_id""",
                    (incident_id, event.get("title", "Untitled"), event.get("category", "world"), event.get("severity", "low"), event.get("k_align_status", "NOT_PROVEN"), event.get("confidence_grade", "G1"), int(event.get("confidence_score", 0)), event.get("action_state", "MONITOR"), first_seen, seen, dumps(enriched), workspace_id),
                )
                for evidence in event.get("evidence", []):
                    source_evidence_id = str(evidence.get("id") or sid(external_id, evidence.get("url"), evidence.get("title")))
                    evidence_id = sid("evidence", workspace_id, source_evidence_id)
                    connection.execute(
                        """INSERT INTO evidence(id,incident_id,source_family,title,url,official,published_at,payload,workspace_id)
                        VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,workspace_id=excluded.workspace_id""",
                        (evidence_id, incident_id, evidence.get("source_family", "unknown"), evidence.get("title", ""), evidence.get("url", ""), int(bool(evidence.get("official"))), evidence.get("published_at"), dumps(evidence), workspace_id),
                    )
                if not old:
                    connection.execute("INSERT INTO timeline(incident_id,event_type,summary,created_at,payload,workspace_id) VALUES(?,?,?,?,?,?)", (incident_id, "DETECTED", "Incident first detected", seen, "{}", workspace_id))
                elif changed:
                    connection.execute("INSERT INTO timeline(incident_id,event_type,summary,created_at,payload,workspace_id) VALUES(?,?,?,?,?,?)", (incident_id, "ASSESSMENT_CHANGED", "Material assessment changed", seen, dumps(event), workspace_id))
            created += int(not old)
            updated += int(changed)
        alerts_created = self.match(incident_ids, workspace_id)
        if actor_user_id:
            self.identity.audit(workspace_id, actor_user_id, "ingest.completed", "incident", outcome="success", metadata={"created": created, "updated": updated})
        return {"ingested": len(incident_ids), "created": created, "updated": updated, "alerts_created": alerts_created, "incident_ids": incident_ids}

    def incident(self, incident_id, with_evidence=True, workspace_id=None):
        workspace_id = self.workspace_id(workspace_id=workspace_id)
        with self.db() as connection:
            row = connection.execute("SELECT * FROM incidents WHERE id=? AND workspace_id=?", (incident_id, workspace_id)).fetchone()
        if not row:
            raise KeyError("incident not found")
        item = dict(row)
        item["payload"] = loads(item.pop("payload"), {})
        # Surface geo fields for maps/list UIs (coords live in payload for most sources).
        payload = item["payload"] if isinstance(item.get("payload"), dict) else {}
        for key in ("latitude", "longitude", "location_name", "country", "what_changed", "why_it_matters",
                    "strongest_counterargument", "falsifier", "k_align_status", "independent_origins",
                    "score_components", "action_state"):
            if item.get(key) in (None, "", "Geolocation pending") and payload.get(key) not in (None, ""):
                item[key] = payload.get(key)
        if item.get("action") in (None, "") and payload.get("action_state"):
            item["action"] = payload.get("action_state")
        if with_evidence:
            with self.db() as connection:
                rows = connection.execute("SELECT * FROM evidence WHERE incident_id=? AND workspace_id=? ORDER BY published_at DESC", (incident_id, workspace_id)).fetchall()
            item["evidence"] = [dict(value) for value in rows]
        return item

    def incidents(self, q="", severity="", limit=100, workspace_id=None):
        workspace_id = self.workspace_id(workspace_id=workspace_id)
        sql = "SELECT id FROM incidents WHERE workspace_id=?"
        args = [workspace_id]
        if q:
            sql += " AND lower(title) LIKE ?"
            args.append("%" + q.lower() + "%")
        if severity:
            sql += " AND severity=?"
            args.append(severity)
        sql += " ORDER BY last_seen DESC LIMIT ?"
        args.append(max(1, min(500, int(limit))))
        with self.db() as connection:
            rows = connection.execute(sql, args).fetchall()
        return [self.incident(row["id"], False, workspace_id) for row in rows]

    def timeline(self, incident_id, workspace_id=None):
        workspace_id = self.workspace_id(workspace_id=workspace_id)
        with self.db() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM timeline WHERE incident_id=? AND workspace_id=? ORDER BY id", (incident_id, workspace_id)).fetchall()]

    def graph(self, incident_id, workspace_id=None):
        workspace_id = self.workspace_id(workspace_id=workspace_id)
        incident = self.incident(incident_id, True, workspace_id)
        nodes = [{"id": incident_id, "type": "incident", "label": incident["title"]}]
        edges = []
        families = set()
        for evidence in incident["evidence"]:
            family_id = "source:" + evidence["source_family"]
            evidence_id = "evidence:" + evidence["id"]
            if family_id not in families:
                nodes.append({"id": family_id, "type": "source", "label": evidence["source_family"]})
                families.add(family_id)
            nodes.append({"id": evidence_id, "type": "evidence", "label": evidence["title"], "url": evidence["url"], "official": bool(evidence["official"])})
            edges.extend([{"from": family_id, "to": evidence_id, "type": "PUBLISHED"}, {"from": evidence_id, "to": incident_id, "type": "SUPPORTS"}])
        return {"incident_id": incident_id, "nodes": nodes, "edges": edges}

    def add_note(self, incident_id, user_id, body, workspace_id=None):
        workspace_id = self.workspace_id(user_id, workspace_id)
        self.incident(incident_id, False, workspace_id)
        body = body.strip()
        if not body:
            raise ValueError("body required")
        note_id = sid("note", workspace_id, incident_id, user_id, now(), secrets.token_hex(4))
        created = now()
        with self.db() as connection:
            connection.execute("INSERT INTO notes(id,incident_id,user_id,body,created_at,workspace_id) VALUES(?,?,?,?,?,?)", (note_id, incident_id, user_id, body, created, workspace_id))
            connection.execute("INSERT INTO timeline(incident_id,event_type,summary,created_at,payload,workspace_id) VALUES(?,?,?,?,?,?)", (incident_id, "ANALYST_NOTE", body[:240], created, dumps({"note_id": note_id}), workspace_id))
        self.identity.audit(workspace_id, user_id, "note.created", "incident", incident_id)
        return {"id": note_id, "incident_id": incident_id, "body": body, "created_at": created, "workspace_id": workspace_id}

    def match(self, incident_ids, workspace_id):
        made = 0
        with self.db() as connection:
            watchlists = connection.execute("SELECT * FROM watchlists WHERE workspace_id=?", (workspace_id,)).fetchall()
        for watchlist in watchlists:
            categories = loads(watchlist["categories"], [])
            severities = loads(watchlist["severities"], [])
            terms = [term.strip().lower() for term in watchlist["query"].replace(" OR ", "|").split("|") if term.strip()]
            for incident_id in incident_ids:
                incident = self.incident(incident_id, False, workspace_id)
                haystack = (incident["title"] + " " + dumps(incident["payload"])).lower()
                if incident["confidence"] < watchlist["min_confidence"] or (categories and incident["category"] not in categories) or (severities and incident["severity"] not in severities) or (terms and not any(term in haystack for term in terms)):
                    continue
                try:
                    with self.db() as connection:
                        connection.execute("INSERT INTO alerts(id,user_id,watchlist_id,incident_id,created_at,workspace_id) VALUES(?,?,?,?,?,?)", (sid("alert", workspace_id, watchlist["id"], incident_id), watchlist["user_id"], watchlist["id"], incident_id, now(), workspace_id))
                    made += 1
                except DatabaseIntegrityError:
                    pass
        return made

    def alerts(self, user_id, workspace_id=None):
        workspace_id = self.workspace_id(user_id, workspace_id)
        with self.db() as connection:
            return [dict(row) for row in connection.execute("""SELECT a.*,w.name watchlist_name,i.title incident_title,i.severity,i.confidence
            FROM alerts a JOIN watchlists w ON w.id=a.watchlist_id JOIN incidents i ON i.id=a.incident_id
            WHERE a.user_id=? AND a.workspace_id=? ORDER BY a.created_at DESC""", (user_id, workspace_id)).fetchall()]
