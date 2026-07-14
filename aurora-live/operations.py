from __future__ import annotations

import ipaddress
import secrets
import urllib.parse

from storage import now, sid


class Operations:
    def __init__(self, store):
        self.store = store
        self.init()

    def init(self):
        with self.store.db() as connection:
            connection.executescript("""
            CREATE TABLE IF NOT EXISTS cases(id TEXT PRIMARY KEY,user_id TEXT,title TEXT,description TEXT,status TEXT,priority TEXT,created_at TEXT,updated_at TEXT);
            CREATE TABLE IF NOT EXISTS case_incidents(case_id TEXT,incident_id TEXT,added_at TEXT,PRIMARY KEY(case_id,incident_id));
            CREATE TABLE IF NOT EXISTS case_notes(id TEXT PRIMARY KEY,case_id TEXT,user_id TEXT,body TEXT,created_at TEXT);
            CREATE TABLE IF NOT EXISTS webhooks(id TEXT PRIMARY KEY,user_id TEXT,name TEXT,url TEXT,active INTEGER,created_at TEXT);
            CREATE TABLE IF NOT EXISTS deliveries(id TEXT PRIMARY KEY,alert_id TEXT,webhook_id TEXT,status TEXT,attempts INTEGER,last_error TEXT,delivered_at TEXT,updated_at TEXT,UNIQUE(alert_id,webhook_id));
            CREATE INDEX IF NOT EXISTS idx_cases_user ON cases(user_id,updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_deliveries_status ON deliveries(status,updated_at);
            """)
        if "acknowledged_at" not in self.store.database.column_names("alerts"):
            with self.store.db() as connection:
                connection.execute("ALTER TABLE alerts ADD COLUMN acknowledged_at TEXT")
        for table in ("cases", "case_incidents", "case_notes", "webhooks", "deliveries"):
            if "workspace_id" not in self.store.database.column_names(table):
                with self.store.db() as connection:
                    connection.execute(f"ALTER TABLE {table} ADD COLUMN workspace_id TEXT")
        default = self.store.identity.default_workspace
        with self.store.db() as connection:
            for table in ("cases", "case_incidents", "case_notes", "webhooks", "deliveries"):
                connection.execute(f"UPDATE {table} SET workspace_id=? WHERE workspace_id IS NULL", (default,))
            connection.executescript("""
            CREATE INDEX IF NOT EXISTS idx_cases_workspace ON cases(workspace_id,user_id,updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_webhooks_workspace ON webhooks(workspace_id,user_id,created_at);
            CREATE INDEX IF NOT EXISTS idx_deliveries_workspace ON deliveries(workspace_id,status,updated_at);
            """)

    def workspace(self, user_id=None, workspace_id=None):
        return self.store.workspace_id(user_id, workspace_id)

    def incidents(self, query="", severity="", category="", status="", grade="", action="", min_confidence=0, limit=100, offset=0, workspace_id=None):
        workspace_id = self.workspace(workspace_id=workspace_id)
        sql = "SELECT id FROM incidents WHERE workspace_id=? AND confidence>=?"
        args = [workspace_id, max(0, min(100, int(min_confidence)))]
        if query:
            sql += " AND (lower(title) LIKE ? OR lower(payload) LIKE ?)"
            needle = "%" + query.lower() + "%"
            args.extend([needle, needle])
        for column, value in (("severity", severity), ("category", category), ("status", status), ("grade", grade), ("action", action)):
            if value:
                sql += f" AND {column}=?"
                args.append(value)
        sql += " ORDER BY last_seen DESC LIMIT ? OFFSET ?"
        args.extend([max(1, min(500, int(limit))), max(0, int(offset))])
        with self.store.db() as connection:
            rows = connection.execute(sql, args).fetchall()
        return [self.store.incident(row["id"], False, workspace_id) for row in rows]

    def acknowledge_alert(self, user_id, alert_id, workspace_id=None):
        workspace_id = self.workspace(user_id, workspace_id)
        stamp = now()
        with self.store.db() as connection:
            changed = connection.execute("UPDATE alerts SET acknowledged_at=? WHERE id=? AND user_id=? AND workspace_id=?", (stamp, alert_id, user_id, workspace_id)).rowcount
        if not changed:
            raise KeyError("alert not found")
        self.store.identity.audit(workspace_id, user_id, "alert.acknowledged", "alert", alert_id)
        return {"id": alert_id, "acknowledged_at": stamp}

    def alerts(self, user_id, unacknowledged=False, workspace_id=None):
        workspace_id = self.workspace(user_id, workspace_id)
        sql = """SELECT a.*,w.name watchlist_name,i.title incident_title,i.severity,i.confidence,i.category,i.status,i.grade,i.action
        FROM alerts a JOIN watchlists w ON w.id=a.watchlist_id JOIN incidents i ON i.id=a.incident_id
        WHERE a.user_id=? AND a.workspace_id=?"""
        if unacknowledged:
            sql += " AND a.acknowledged_at IS NULL"
        sql += " ORDER BY a.created_at DESC"
        with self.store.db() as connection:
            return [dict(row) for row in connection.execute(sql, (user_id, workspace_id)).fetchall()]

    def _webhook_url(self, value):
        url = str(value or "").strip()
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("webhook URL must use https")
        host = parsed.hostname.lower()
        if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
            raise ValueError("local webhook destinations are not allowed")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address and (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved):
            raise ValueError("private webhook destinations are not allowed")
        return url

    def add_webhook(self, user_id, payload, workspace_id=None):
        workspace_id = self.workspace(user_id, workspace_id)
        name = str(payload.get("name", "")).strip()
        if not name:
            raise ValueError("name required")
        url = self._webhook_url(payload.get("url", ""))
        webhook_id = sid("webhook", workspace_id, user_id, name, url, now(), secrets.token_hex(4))
        created = now()
        with self.store.db() as connection:
            connection.execute("INSERT INTO webhooks(id,user_id,name,url,active,created_at,workspace_id) VALUES(?,?,?,?,?,?,?)", (webhook_id, user_id, name, url, 1, created, workspace_id))
        self.store.identity.audit(workspace_id, user_id, "webhook.created", "webhook", webhook_id)
        return self.webhook(user_id, webhook_id, workspace_id)

    def webhook(self, user_id, webhook_id, workspace_id=None):
        workspace_id = self.workspace(user_id, workspace_id)
        with self.store.db() as connection:
            row = connection.execute("SELECT * FROM webhooks WHERE user_id=? AND workspace_id=? AND id=?", (user_id, workspace_id, webhook_id)).fetchone()
        if not row:
            raise KeyError("webhook not found")
        item = dict(row)
        item["active"] = bool(item["active"])
        return item

    def webhooks(self, user_id, workspace_id=None):
        workspace_id = self.workspace(user_id, workspace_id)
        with self.store.db() as connection:
            rows = connection.execute("SELECT id FROM webhooks WHERE user_id=? AND workspace_id=? ORDER BY created_at DESC", (user_id, workspace_id)).fetchall()
        return [self.webhook(user_id, row["id"], workspace_id) for row in rows]

    def delete_webhook(self, user_id, webhook_id, workspace_id=None):
        workspace_id = self.workspace(user_id, workspace_id)
        with self.store.db() as connection:
            changed = connection.execute("DELETE FROM webhooks WHERE user_id=? AND workspace_id=? AND id=?", (user_id, workspace_id, webhook_id)).rowcount
        if changed:
            self.store.identity.audit(workspace_id, user_id, "webhook.deleted", "webhook", webhook_id)
        return changed > 0

    def queue_deliveries(self, user_id, alert_id, workspace_id=None):
        workspace_id = self.workspace(user_id, workspace_id)
        with self.store.db() as connection:
            alert = connection.execute("SELECT 1 FROM alerts WHERE id=? AND user_id=? AND workspace_id=?", (alert_id, user_id, workspace_id)).fetchone()
            if not alert:
                raise KeyError("alert not found")
            hooks = connection.execute("SELECT id FROM webhooks WHERE user_id=? AND workspace_id=? AND active=1", (user_id, workspace_id)).fetchall()
            for hook in hooks:
                connection.execute("""INSERT INTO deliveries(id,alert_id,webhook_id,status,attempts,last_error,delivered_at,updated_at,workspace_id)
                VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(alert_id,webhook_id) DO NOTHING""", (sid("delivery", workspace_id, alert_id, hook["id"]), alert_id, hook["id"], "pending", 0, None, None, now(), workspace_id))

    def pending_deliveries(self, user_id, limit=100, workspace_id=None):
        workspace_id = self.workspace(user_id, workspace_id)
        sql = """SELECT d.*,w.url,w.name webhook_name,a.user_id,a.watchlist_id,a.incident_id,
        i.title incident_title,i.severity,i.confidence,i.category,i.status AS incident_status,i.grade,i.action
        FROM deliveries d JOIN webhooks w ON w.id=d.webhook_id JOIN alerts a ON a.id=d.alert_id JOIN incidents i ON i.id=a.incident_id
        WHERE a.user_id=? AND d.workspace_id=? AND d.status IN ('pending','failed') AND d.attempts<5 AND w.active=1 ORDER BY d.updated_at LIMIT ?"""
        with self.store.db() as connection:
            return [dict(row) for row in connection.execute(sql, (user_id, workspace_id, max(1, min(500, int(limit))))).fetchall()]

    def record_delivery(self, delivery_id, success, error=""):
        with self.store.db() as connection:
            row = connection.execute("SELECT attempts FROM deliveries WHERE id=?", (delivery_id,)).fetchone()
            if not row:
                raise KeyError("delivery not found")
            connection.execute("UPDATE deliveries SET status=?,attempts=?,last_error=?,delivered_at=?,updated_at=? WHERE id=?", ("delivered" if success else "failed", int(row["attempts"]) + 1, None if success else error[:500], now() if success else None, now(), delivery_id))

    def create_case(self, user_id, payload, workspace_id=None):
        workspace_id = self.workspace(user_id, workspace_id)
        title = str(payload.get("title", "")).strip()
        if not title:
            raise ValueError("title required")
        status = str(payload.get("status", "open")).lower()
        priority = str(payload.get("priority", "normal")).lower()
        if status not in {"open", "monitoring", "closed"}:
            raise ValueError("invalid case status")
        if priority not in {"low", "normal", "high", "critical"}:
            raise ValueError("invalid case priority")
        case_id = sid("case", workspace_id, user_id, title, now(), secrets.token_hex(4))
        stamp = now()
        with self.store.db() as connection:
            connection.execute("INSERT INTO cases(id,user_id,title,description,status,priority,created_at,updated_at,workspace_id) VALUES(?,?,?,?,?,?,?,?,?)", (case_id, user_id, title, str(payload.get("description", "")).strip(), status, priority, stamp, stamp, workspace_id))
        self.store.identity.audit(workspace_id, user_id, "case.created", "case", case_id)
        return self.case(user_id, case_id, workspace_id)

    def cases(self, user_id, workspace_id=None):
        workspace_id = self.workspace(user_id, workspace_id)
        with self.store.db() as connection:
            rows = connection.execute("SELECT id FROM cases WHERE user_id=? AND workspace_id=? ORDER BY updated_at DESC", (user_id, workspace_id)).fetchall()
        return [self.case(user_id, row["id"], workspace_id) for row in rows]

    def case(self, user_id, case_id, workspace_id=None):
        workspace_id = self.workspace(user_id, workspace_id)
        with self.store.db() as connection:
            row = connection.execute("SELECT * FROM cases WHERE user_id=? AND workspace_id=? AND id=?", (user_id, workspace_id, case_id)).fetchone()
            incidents = connection.execute("""SELECT i.id,i.title,i.category,i.severity,i.status,i.grade,i.confidence,i.action,ci.added_at
            FROM case_incidents ci JOIN incidents i ON i.id=ci.incident_id WHERE ci.case_id=? AND ci.workspace_id=? ORDER BY ci.added_at DESC""", (case_id, workspace_id)).fetchall()
            notes = connection.execute("SELECT id,user_id,body,created_at FROM case_notes WHERE case_id=? AND workspace_id=? ORDER BY created_at", (case_id, workspace_id)).fetchall()
        if not row:
            raise KeyError("case not found")
        item = dict(row)
        item["incidents"] = [dict(value) for value in incidents]
        item["notes"] = [dict(value) for value in notes]
        return item

    def add_case_incident(self, user_id, case_id, incident_id, workspace_id=None):
        workspace_id = self.workspace(user_id, workspace_id)
        self.case(user_id, case_id, workspace_id)
        self.store.incident(incident_id, False, workspace_id)
        with self.store.db() as connection:
            connection.execute("INSERT INTO case_incidents(case_id,incident_id,added_at,workspace_id) VALUES(?,?,?,?) ON CONFLICT(case_id,incident_id) DO NOTHING", (case_id, incident_id, now(), workspace_id))
            connection.execute("UPDATE cases SET updated_at=? WHERE id=? AND workspace_id=?", (now(), case_id, workspace_id))
        return self.case(user_id, case_id, workspace_id)

    def remove_case_incident(self, user_id, case_id, incident_id, workspace_id=None):
        workspace_id = self.workspace(user_id, workspace_id)
        self.case(user_id, case_id, workspace_id)
        with self.store.db() as connection:
            changed = connection.execute("DELETE FROM case_incidents WHERE case_id=? AND incident_id=? AND workspace_id=?", (case_id, incident_id, workspace_id)).rowcount
            connection.execute("UPDATE cases SET updated_at=? WHERE id=? AND workspace_id=?", (now(), case_id, workspace_id))
        return changed > 0

    def add_case_note(self, user_id, case_id, body, workspace_id=None):
        workspace_id = self.workspace(user_id, workspace_id)
        self.case(user_id, case_id, workspace_id)
        body = body.strip()
        if not body:
            raise ValueError("body required")
        note_id = sid("case-note", workspace_id, case_id, user_id, now(), secrets.token_hex(4))
        stamp = now()
        with self.store.db() as connection:
            connection.execute("INSERT INTO case_notes(id,case_id,user_id,body,created_at,workspace_id) VALUES(?,?,?,?,?,?)", (note_id, case_id, user_id, body, stamp, workspace_id))
            connection.execute("UPDATE cases SET updated_at=? WHERE id=? AND workspace_id=?", (stamp, case_id, workspace_id))
        self.store.identity.audit(workspace_id, user_id, "case.note.created", "case", case_id)
        return {"id": note_id, "case_id": case_id, "body": body, "created_at": stamp}

    def stats(self, user_id, workspace_id=None):
        workspace_id = self.workspace(user_id, workspace_id)
        with self.store.db() as connection:
            return {
                "incidents": connection.execute("SELECT COUNT(*) FROM incidents WHERE workspace_id=?", (workspace_id,)).fetchone()[0],
                "alerts_unacknowledged": connection.execute("SELECT COUNT(*) FROM alerts WHERE user_id=? AND workspace_id=? AND acknowledged_at IS NULL", (user_id, workspace_id)).fetchone()[0],
                "watchlists": connection.execute("SELECT COUNT(*) FROM watchlists WHERE user_id=? AND workspace_id=?", (user_id, workspace_id)).fetchone()[0],
                "cases": connection.execute("SELECT COUNT(*) FROM cases WHERE user_id=? AND workspace_id=?", (user_id, workspace_id)).fetchone()[0],
                "pending_deliveries": connection.execute("""SELECT COUNT(*) FROM deliveries d JOIN alerts a ON a.id=d.alert_id
                WHERE a.user_id=? AND d.workspace_id=? AND d.status IN ('pending','failed') AND d.attempts<5""", (user_id, workspace_id)).fetchone()[0],
            }
