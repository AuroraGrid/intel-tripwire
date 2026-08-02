from __future__ import annotations

import hashlib
import json
import re
import secrets
from contextvars import ContextVar
from datetime import datetime, timezone

CURRENT_WORKSPACE = ContextVar("aurora_workspace", default=None)
ROLES = {
    "viewer": {"read"},
    "analyst": {"read", "write"},
    "admin": {"read", "write", "ingest", "admin", "workers"},
    "owner": {"read", "write", "ingest", "admin", "workers", "owner"},
}


def now(): return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
def sid(*values): return hashlib.sha256("|".join(map(str, values)).encode()).hexdigest()[:24]
def slug(value):
    value = re.sub(r"[^a-z0-9]+", "-", str(value).strip().lower()).strip("-")
    if not value: raise ValueError("valid slug required")
    return value[:64]


class Identity:
    def __init__(self, store):
        self.store = store
        self.init()

    def init(self):
        with self.store.db() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS organizations(id TEXT PRIMARY KEY,name TEXT NOT NULL,slug TEXT UNIQUE NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS workspaces(id TEXT PRIMARY KEY,organization_id TEXT NOT NULL,name TEXT NOT NULL,slug TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(organization_id,slug));
            CREATE TABLE IF NOT EXISTS memberships(user_id TEXT NOT NULL,workspace_id TEXT NOT NULL,role TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(user_id,workspace_id));
            CREATE TABLE IF NOT EXISTS api_tokens(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,workspace_id TEXT NOT NULL,name TEXT NOT NULL,token_hash TEXT UNIQUE NOT NULL,expires_at TEXT,revoked_at TEXT,last_used_at TEXT,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS audit_events(id TEXT PRIMARY KEY,workspace_id TEXT,actor_user_id TEXT,action TEXT NOT NULL,resource_type TEXT,resource_id TEXT,outcome TEXT NOT NULL,metadata TEXT,created_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_memberships_workspace ON memberships(workspace_id,role);
            CREATE INDEX IF NOT EXISTS idx_tokens_lookup ON api_tokens(token_hash,revoked_at,expires_at);
            CREATE INDEX IF NOT EXISTS idx_audit_workspace_time ON audit_events(workspace_id,created_at);
            """)
        self.org_id = sid("organization", "legacy")
        self.default_workspace = sid("workspace", self.org_id, "default")
        stamp = now()
        with self.store.db() as c:
            c.execute("INSERT INTO organizations(id,name,slug,created_at) VALUES(?,?,?,?) ON CONFLICT(id) DO NOTHING", (self.org_id, "AURORA", "aurora", stamp))
            c.execute("INSERT INTO workspaces(id,organization_id,name,slug,created_at) VALUES(?,?,?,?,?) ON CONFLICT(id) DO NOTHING", (self.default_workspace, self.org_id, "Default", "default", stamp))
            for user in c.execute("SELECT id,role,token_hash,created_at FROM users").fetchall():
                role = user["role"] if user["role"] in ROLES else "analyst"
                created = user["created_at"] or stamp
                c.execute("INSERT INTO memberships(user_id,workspace_id,role,created_at) VALUES(?,?,?,?) ON CONFLICT(user_id,workspace_id) DO NOTHING", (user["id"], self.default_workspace, role, created))
                if user["token_hash"]:
                    c.execute("INSERT INTO api_tokens(id,user_id,workspace_id,name,token_hash,created_at) VALUES(?,?,?,?,?,?) ON CONFLICT(token_hash) DO NOTHING", (sid("legacy-token", user["id"]), user["id"], self.default_workspace, "legacy", user["token_hash"], created))
        with self.store.db() as c:
            if self.store.backend == "sqlite":
                c.executescript("CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_events BEGIN SELECT RAISE(ABORT,'audit events are immutable'); END; CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_events BEGIN SELECT RAISE(ABORT,'audit events are immutable'); END;")
            else:
                c.execute("CREATE OR REPLACE RULE audit_no_update AS ON UPDATE TO audit_events DO INSTEAD NOTHING")
                c.execute("CREATE OR REPLACE RULE audit_no_delete AS ON DELETE TO audit_events DO INSTEAD NOTHING")

    def workspace_id(self, user_id=None):
        current = CURRENT_WORKSPACE.get()
        if current: return current
        if user_id:
            with self.store.db() as c: row = c.execute("SELECT workspace_id FROM memberships WHERE user_id=? ORDER BY created_at LIMIT 1", (user_id,)).fetchone()
            if row: return row["workspace_id"]
        return self.default_workspace

    def permissions(self, role): return sorted(ROLES.get(role, set()))

    def register(self, user_id, role, digest, created):
        with self.store.db() as c:
            c.execute("INSERT INTO memberships(user_id,workspace_id,role,created_at) VALUES(?,?,?,?) ON CONFLICT(user_id,workspace_id) DO UPDATE SET role=excluded.role", (user_id, self.default_workspace, role, created))
            c.execute("INSERT INTO api_tokens(id,user_id,workspace_id,name,token_hash,created_at) VALUES(?,?,?,?,?,?) ON CONFLICT(token_hash) DO NOTHING", (sid("token", user_id, created), user_id, self.default_workspace, "bootstrap", digest, created))
        return self.default_workspace

    def auth(self, token):
        if not token: return None
        digest, stamp = hashlib.sha256(token.encode()).hexdigest(), now()
        with self.store.db() as c:
            row = c.execute("""SELECT t.id token_id,t.user_id,t.workspace_id,t.name token_name,t.expires_at,t.revoked_at,
            u.email,m.role workspace_role,w.organization_id,w.name workspace_name FROM api_tokens t JOIN users u ON u.id=t.user_id
            JOIN memberships m ON m.user_id=t.user_id AND m.workspace_id=t.workspace_id JOIN workspaces w ON w.id=t.workspace_id
            WHERE t.token_hash=?""", (digest,)).fetchone()
            if not row or row["revoked_at"] or (row["expires_at"] and row["expires_at"] <= stamp): return None
            c.execute("UPDATE api_tokens SET last_used_at=? WHERE id=?", (stamp, row["token_id"]))
        user = dict(row)
        user["id"] = user.pop("user_id")
        user["permissions"] = self.permissions(user["workspace_role"])
        user["role"] = "admin" if user["workspace_role"] == "owner" else user["workspace_role"]
        CURRENT_WORKSPACE.set(user["workspace_id"])
        self.audit(user["workspace_id"], user["id"], "authentication.success", "api_token", user["token_id"])
        return user

    def require(self, user, permission):
        if permission not in set(user.get("permissions") or []):
            self.audit(user.get("workspace_id"), user.get("id"), "authorization.denied", "permission", permission, "denied")
            raise PermissionError("insufficient permission")

    def create_workspace(self, actor, name, slug_value=""):
        self.require(actor, "owner")
        name = str(name).strip()
        if not name: raise ValueError("workspace name required")
        value = slug(slug_value or name)
        workspace_id, stamp = sid("workspace", actor["organization_id"], value), now()
        with self.store.db() as c:
            c.execute("INSERT INTO workspaces(id,organization_id,name,slug,created_at) VALUES(?,?,?,?,?)", (workspace_id, actor["organization_id"], name, value, stamp))
            c.execute("INSERT INTO memberships(user_id,workspace_id,role,created_at) VALUES(?,?,?,?)", (actor["id"], workspace_id, "owner", stamp))
        self.audit(actor["workspace_id"], actor["id"], "workspace.created", "workspace", workspace_id, metadata={"name": name})
        return {"id": workspace_id, "organization_id": actor["organization_id"], "name": name, "slug": value, "created_at": stamp}

    def workspaces(self, user_id):
        with self.store.db() as c: rows = c.execute("SELECT w.*,m.role FROM workspaces w JOIN memberships m ON m.workspace_id=w.id WHERE m.user_id=? ORDER BY w.created_at", (user_id,)).fetchall()
        return [dict(row, permissions=self.permissions(row["role"])) for row in rows]

    def add_membership(self, actor, user_id, role, workspace_id=None):
        self.require(actor, "admin")
        workspace_id = workspace_id or actor["workspace_id"]
        if role not in ROLES: raise ValueError("invalid membership role")
        if role == "owner" and "owner" not in actor["permissions"]: raise PermissionError("only owners can grant owner")
        with self.store.db() as c:
            workspace = c.execute("SELECT organization_id FROM workspaces WHERE id=?", (workspace_id,)).fetchone()
            if not workspace or workspace["organization_id"] != actor["organization_id"]: raise KeyError("workspace not found")
            if not c.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone(): raise KeyError("user not found")
            c.execute("INSERT INTO memberships(user_id,workspace_id,role,created_at) VALUES(?,?,?,?) ON CONFLICT(user_id,workspace_id) DO UPDATE SET role=excluded.role", (user_id, workspace_id, role, now()))
        self.audit(workspace_id, actor["id"], "membership.upserted", "user", user_id, metadata={"role": role})
        return {"user_id": user_id, "workspace_id": workspace_id, "role": role, "permissions": self.permissions(role)}

    def memberships(self, actor):
        self.require(actor, "admin")
        with self.store.db() as c: rows = c.execute("SELECT m.user_id,u.email,m.workspace_id,m.role,m.created_at FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.workspace_id=? ORDER BY u.email", (actor["workspace_id"],)).fetchall()
        return [dict(row, permissions=self.permissions(row["role"])) for row in rows]

    def issue_token(self, actor, user_id=None, name="api", expires_at=None, workspace_id=None):
        self.require(actor, "admin")
        user_id, workspace_id = user_id or actor["id"], workspace_id or actor["workspace_id"]
        with self.store.db() as c:
            if not c.execute("SELECT 1 FROM memberships WHERE user_id=? AND workspace_id=?", (user_id, workspace_id)).fetchone(): raise KeyError("membership not found")
        secret, stamp = secrets.token_urlsafe(32), now()
        token_id = sid("api-token", user_id, workspace_id, stamp, secrets.token_hex(4))
        with self.store.db() as c: c.execute("INSERT INTO api_tokens(id,user_id,workspace_id,name,token_hash,expires_at,created_at) VALUES(?,?,?,?,?,?,?)", (token_id, user_id, workspace_id, str(name)[:80], hashlib.sha256(secret.encode()).hexdigest(), expires_at, stamp))
        self.audit(workspace_id, actor["id"], "token.issued", "api_token", token_id, metadata={"user_id": user_id, "expires_at": expires_at})
        return {"id": token_id, "user_id": user_id, "workspace_id": workspace_id, "name": str(name)[:80], "expires_at": expires_at, "created_at": stamp}, secret

    def issue_session_secret(self, user_id: str, workspace_id: str, name: str = "password-session"):
        """Issue an API token without admin actor — used by shared password login."""
        with self.store.db() as c:
            if not c.execute("SELECT 1 FROM memberships WHERE user_id=? AND workspace_id=?", (user_id, workspace_id)).fetchone():
                raise KeyError("membership not found")
        secret, stamp = secrets.token_urlsafe(32), now()
        token_id = sid("api-token", user_id, workspace_id, stamp, secrets.token_hex(4))
        with self.store.db() as c:
            c.execute(
                "INSERT INTO api_tokens(id,user_id,workspace_id,name,token_hash,expires_at,created_at) VALUES(?,?,?,?,?,?,?)",
                (token_id, user_id, workspace_id, str(name)[:80], hashlib.sha256(secret.encode()).hexdigest(), None, stamp),
            )
        self.audit(workspace_id, user_id, "token.issued", "api_token", token_id, metadata={"name": name, "via": "password_login"})
        return secret

    def tokens(self, actor):
        self.require(actor, "admin")
        with self.store.db() as c: rows = c.execute("SELECT id,user_id,workspace_id,name,expires_at,revoked_at,last_used_at,created_at FROM api_tokens WHERE workspace_id=? ORDER BY created_at DESC", (actor["workspace_id"],)).fetchall()
        return [dict(row) for row in rows]

    def revoke(self, actor, token_id):
        self.require(actor, "admin")
        stamp = now()
        with self.store.db() as c: changed = c.execute("UPDATE api_tokens SET revoked_at=? WHERE id=? AND workspace_id=? AND revoked_at IS NULL", (stamp, token_id, actor["workspace_id"])).rowcount
        if not changed: raise KeyError("token not found")
        self.audit(actor["workspace_id"], actor["id"], "token.revoked", "api_token", token_id)
        return {"id": token_id, "revoked_at": stamp}

    def rotate(self, actor, token_id):
        with self.store.db() as c: row = c.execute("SELECT user_id,name,expires_at FROM api_tokens WHERE id=? AND workspace_id=?", (token_id, actor["workspace_id"])).fetchone()
        if not row: raise KeyError("token not found")
        self.revoke(actor, token_id)
        return self.issue_token(actor, row["user_id"], row["name"] + " rotated", row["expires_at"])

    def audit(self, workspace_id, actor_user_id, action, resource_type="", resource_id="", outcome="success", metadata=None):
        stamp = now()
        event_id = sid("audit", workspace_id, actor_user_id, action, stamp, secrets.token_hex(4))
        with self.store.db() as c: c.execute("INSERT INTO audit_events(id,workspace_id,actor_user_id,action,resource_type,resource_id,outcome,metadata,created_at) VALUES(?,?,?,?,?,?,?,?,?)", (event_id, workspace_id, actor_user_id, action, resource_type or None, resource_id or None, outcome, json.dumps(metadata or {}, separators=(",", ":"), sort_keys=True), stamp))
        return event_id

    def audits(self, actor, limit=100):
        self.require(actor, "admin")
        with self.store.db() as c: rows = c.execute("SELECT * FROM audit_events WHERE workspace_id=? ORDER BY created_at DESC LIMIT ?", (actor["workspace_id"], max(1, min(500, int(limit))))).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            try: item["metadata"] = json.loads(item.get("metadata") or "{}")
            except Exception: item["metadata"] = {}
            output.append(item)
        return output
