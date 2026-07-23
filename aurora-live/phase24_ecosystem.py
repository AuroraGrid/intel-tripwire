from __future__ import annotations

import base64
import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from phase15_mesh import stable_id


VALID_SCOPES = {
    "read",
    "write",
    "forecasts:write",
    "collaboration:write",
}
DEFAULT_SCOPES = {"read"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def loads(value: Any, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except (TypeError, json.JSONDecodeError):
        return default


class DeveloperEcosystem:
    """Scoped service credentials, discovery, cursors, and idempotency."""

    def __init__(self, store):
        self.store = store
        self._init_schema()

    def _init_schema(self) -> None:
        with self.store.db() as connection:
            connection.executescript("""
            CREATE TABLE IF NOT EXISTS developer_clients(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
                name TEXT NOT NULL, prefix TEXT NOT NULL,
                secret_hash TEXT NOT NULL, scopes TEXT NOT NULL,
                actor_user_id TEXT NOT NULL, actor_role TEXT NOT NULL,
                active INTEGER NOT NULL, expires_at TEXT, last_used_at TEXT,
                created_by TEXT NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(workspace_id,name)
            );
            CREATE TABLE IF NOT EXISTS developer_idempotency(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
                client_id TEXT NOT NULL, idempotency_key TEXT NOT NULL,
                operation TEXT NOT NULL, request_hash TEXT NOT NULL,
                response_status INTEGER NOT NULL, response_body TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(workspace_id,client_id,idempotency_key)
            );
            CREATE TABLE IF NOT EXISTS developer_requests(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
                client_id TEXT NOT NULL, surface TEXT NOT NULL,
                operation TEXT NOT NULL, outcome TEXT NOT NULL,
                latency_ms REAL NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_developer_clients_workspace
                ON developer_clients(workspace_id,active,name);
            CREATE INDEX IF NOT EXISTS idx_developer_idempotency_lookup
                ON developer_idempotency(
                    workspace_id,client_id,idempotency_key
                );
            CREATE INDEX IF NOT EXISTS idx_developer_requests_workspace
                ON developer_requests(workspace_id,created_at);
            """)

    def _workspace(self, actor: dict[str, Any]) -> str:
        return str(actor["workspace_id"])

    def _actor(self, actor: dict[str, Any]) -> str:
        return str(actor.get("id") or "system")

    @staticmethod
    def _digest(secret: str) -> str:
        return hashlib.sha256(secret.encode("utf-8")).hexdigest()

    @staticmethod
    def encode_cursor(offset: int) -> str:
        payload = json.dumps(
            {"offset": max(0, int(offset))}, separators=(",", ":")
        ).encode("utf-8")
        return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

    @staticmethod
    def decode_cursor(cursor: str) -> int:
        if not cursor:
            return 0
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            value = json.loads(
                base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
            )
            return max(0, int(value["offset"]))
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            raise ValueError("invalid cursor")

    def create_client(
        self, actor: dict[str, Any], payload: dict[str, Any]
    ) -> dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        scopes = {
            str(scope).strip().lower()
            for scope in (payload.get("scopes") or DEFAULT_SCOPES)
        }
        if not name:
            raise ValueError("client name required")
        if not scopes or not scopes.issubset(VALID_SCOPES):
            raise ValueError("invalid client scopes")
        expires_at = payload.get("expires_at")
        if expires_at and parse_time(str(expires_at)) <= datetime.now(timezone.utc):
            raise ValueError("expires_at must be in the future")
        client_id = stable_id(
            "developer-client", self._workspace(actor), name.lower()
        )
        secret = "aurora_sk_" + secrets.token_urlsafe(32)
        prefix = secret[:18]
        stamp = now()
        with self.store.db() as connection:
            existing = connection.execute(
                "SELECT id FROM developer_clients WHERE workspace_id=? AND name=?",
                (self._workspace(actor), name),
            ).fetchone()
            if existing:
                raise ValueError("developer client name already exists")
            connection.execute(
                """INSERT INTO developer_clients(
                id,workspace_id,name,prefix,secret_hash,scopes,actor_user_id,
                actor_role,active,expires_at,last_used_at,created_by,created_at,
                updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    client_id,
                    self._workspace(actor),
                    name,
                    prefix,
                    self._digest(secret),
                    json.dumps(sorted(scopes)),
                    self._actor(actor),
                    str(actor.get("role") or "viewer"),
                    1,
                    str(expires_at) if expires_at else None,
                    None,
                    self._actor(actor),
                    stamp,
                    stamp,
                ),
            )
        self.store.identity.audit(
            self._workspace(actor),
            self._actor(actor),
            "developer.client.created",
            "developer_client",
            client_id,
            metadata={"scopes": sorted(scopes)},
        )
        client = self.client(actor, client_id)
        client["secret"] = secret
        client["warning"] = "This secret is shown once. Store it securely."
        return client

    def client(
        self, actor: dict[str, Any], client_id: str
    ) -> dict[str, Any]:
        with self.store.db() as connection:
            row = connection.execute(
                "SELECT * FROM developer_clients WHERE id=? AND workspace_id=?",
                (client_id, self._workspace(actor)),
            ).fetchone()
        if not row:
            raise KeyError("developer client not found")
        item = dict(row)
        item.pop("secret_hash", None)
        item["scopes"] = loads(item["scopes"], [])
        item["active"] = bool(item["active"])
        return item

    def clients(self, actor: dict[str, Any]) -> list[dict[str, Any]]:
        with self.store.db() as connection:
            rows = connection.execute(
                """SELECT id FROM developer_clients WHERE workspace_id=?
                ORDER BY name""",
                (self._workspace(actor),),
            ).fetchall()
        return [self.client(actor, row["id"]) for row in rows]

    def revoke(
        self, actor: dict[str, Any], client_id: str
    ) -> dict[str, Any]:
        self.client(actor, client_id)
        with self.store.db() as connection:
            connection.execute(
                """UPDATE developer_clients SET active=0,updated_at=?
                WHERE id=? AND workspace_id=?""",
                (now(), client_id, self._workspace(actor)),
            )
        self.store.identity.audit(
            self._workspace(actor),
            self._actor(actor),
            "developer.client.revoked",
            "developer_client",
            client_id,
        )
        return self.client(actor, client_id)

    def authenticate(self, secret: str) -> dict[str, Any]:
        raw = str(secret or "").strip()
        if not raw.startswith("aurora_sk_"):
            raise PermissionError("invalid developer credential")
        digest = self._digest(raw)
        with self.store.db() as connection:
            row = connection.execute(
                "SELECT * FROM developer_clients WHERE secret_hash=?",
                (digest,),
            ).fetchone()
        if not row or not bool(row["active"]):
            raise PermissionError("invalid developer credential")
        expires_at = parse_time(row["expires_at"])
        if expires_at and expires_at <= datetime.now(timezone.utc):
            raise PermissionError("developer credential expired")
        with self.store.db() as connection:
            connection.execute(
                "UPDATE developer_clients SET last_used_at=?,updated_at=? WHERE id=?",
                (now(), now(), row["id"]),
            )
        return {
            "id": row["actor_user_id"],
            "workspace_id": row["workspace_id"],
            "role": row["actor_role"],
            "permissions": [],
            "developer_client_id": row["id"],
            "developer_client_name": row["name"],
            "api_scopes": loads(row["scopes"], []),
        }

    @staticmethod
    def require_scope(actor: dict[str, Any], scope: str) -> None:
        if not actor.get("developer_client_id"):
            return
        scopes = set(actor.get("api_scopes") or [])
        if scope not in scopes and "write" not in scopes:
            raise PermissionError(f"developer credential lacks {scope} scope")

    def idempotent(
        self,
        actor: dict[str, Any],
        key: str,
        operation: str,
        request_payload: dict[str, Any],
        callback: Callable[[], tuple[int, dict[str, Any]]],
    ) -> tuple[int, dict[str, Any], bool]:
        idem_key = str(key or "").strip()
        if not idem_key:
            raise ValueError("Idempotency-Key header required")
        if len(idem_key) > 200:
            raise ValueError("Idempotency-Key is too long")
        client_id = str(
            actor.get("developer_client_id") or f"user:{self._actor(actor)}"
        )
        request_hash = hashlib.sha256(
            json.dumps(
                request_payload, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        with self.store.db() as connection:
            existing = connection.execute(
                """SELECT * FROM developer_idempotency WHERE workspace_id=?
                AND client_id=? AND idempotency_key=?""",
                (self._workspace(actor), client_id, idem_key),
            ).fetchone()
        if existing:
            if (
                existing["operation"] != operation
                or existing["request_hash"] != request_hash
            ):
                raise ValueError(
                    "Idempotency-Key was already used for a different request"
                )
            return (
                int(existing["response_status"]),
                loads(existing["response_body"], {}),
                True,
            )
        status, response = callback()
        record_id = stable_id(
            "developer-idempotency",
            self._workspace(actor),
            client_id,
            idem_key,
        )
        with self.store.db() as connection:
            connection.execute(
                """INSERT INTO developer_idempotency(
                id,workspace_id,client_id,idempotency_key,operation,
                request_hash,response_status,response_body,created_at)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    record_id,
                    self._workspace(actor),
                    client_id,
                    idem_key,
                    operation,
                    request_hash,
                    int(status),
                    json.dumps(response, sort_keys=True),
                    now(),
                ),
            )
        return status, response, False

    def record_request(
        self,
        actor: dict[str, Any],
        surface: str,
        operation: str,
        outcome: str,
        latency_ms: float,
    ) -> None:
        client_id = str(
            actor.get("developer_client_id") or f"user:{self._actor(actor)}"
        )
        stamp = now()
        request_id = stable_id(
            "developer-request",
            self._workspace(actor),
            client_id,
            surface,
            operation,
            stamp,
            secrets.token_hex(4),
        )
        with self.store.db() as connection:
            connection.execute(
                """INSERT INTO developer_requests(
                id,workspace_id,client_id,surface,operation,outcome,
                latency_ms,created_at) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    request_id,
                    self._workspace(actor),
                    client_id,
                    surface,
                    operation,
                    outcome,
                    max(0, float(latency_ms)),
                    stamp,
                ),
            )

    def openapi(self) -> dict[str, Any]:
        read = {
            "/api/v1/detections": "List correlated detections",
            "/api/v1/detections/{id}": "Retrieve a detection and lineage",
            "/api/v1/routes": "List route-risk plans",
            "/api/v1/routes/{id}": "Retrieve a route plan",
            "/api/v1/forecast-candidates": "List forecast candidates",
            "/api/v1/forecast-candidates/{id}": "Retrieve a candidate",
            "/api/v1/events": "Cursor stream of durable fabric events",
            "/api/v1/search": "Search intelligence records",
            "/api/v1/source-health": "Read sensor health and coverage",
            "/api/v1/contradictions": "List open evidence contradictions",
        }
        paths: dict[str, Any] = {
            path: {
                "get": {
                    "summary": summary,
                    "security": [{"bearerAuth": []}, {"apiKeyAuth": []}],
                    "responses": {"200": {"description": "Success"}},
                }
            }
            for path, summary in read.items()
        }
        paths["/api/v1/forecast-candidates/{id}/approve"] = {
            "post": {
                "summary": "Approve a deterministic forecast candidate",
                "security": [{"bearerAuth": []}, {"apiKeyAuth": []}],
                "parameters": [{
                    "name": "Idempotency-Key",
                    "in": "header",
                    "required": True,
                    "schema": {"type": "string"},
                }],
                "responses": {
                    "200": {"description": "Approved"},
                    "409": {"description": "Idempotency conflict"},
                },
            }
        }
        return {
            "openapi": "3.1.0",
            "info": {
                "title": "AURORA LIVE Developer API",
                "version": "24.0.0",
                "description": (
                    "Workspace-isolated intelligence, evidence, routes, "
                    "forecasts, streaming events, and agent discovery."
                ),
            },
            "servers": [{"url": "/"}],
            "components": {
                "securitySchemes": {
                    "bearerAuth": {
                        "type": "http",
                        "scheme": "bearer",
                    },
                    "apiKeyAuth": {
                        "type": "apiKey",
                        "in": "header",
                        "name": "X-AURORA-API-KEY",
                    },
                }
            },
            "paths": paths,
        }

    def agent_manifest(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "name": "AURORA LIVE",
            "description": (
                "Evidence-first global intelligence with provenance, "
                "contradictions, routes, and calibrated forecasts."
            ),
            "authentication": {
                "types": ["bearer", "api_key"],
                "api_key_header": "X-AURORA-API-KEY",
            },
            "discovery": {
                "openapi": "/api/v1/openapi.json",
                "mcp": "/mcp",
                "mcp_manifest": "/mcp/manifest.json",
            },
            "safety": {
                "workspace_isolation": True,
                "audit_logging": True,
                "forecast_approval_required": True,
                "external_ai_required": False,
            },
        }

    def scorecard(self, actor: dict[str, Any]) -> dict[str, Any]:
        with self.store.db() as connection:
            clients = connection.execute(
                """SELECT COUNT(*) total,
                SUM(CASE WHEN active=1 THEN 1 ELSE 0 END) active
                FROM developer_clients WHERE workspace_id=?""",
                (self._workspace(actor),),
            ).fetchone()
            requests = connection.execute(
                """SELECT surface,COUNT(*) total,AVG(latency_ms) average_latency
                FROM developer_requests WHERE workspace_id=?
                GROUP BY surface""",
                (self._workspace(actor),),
            ).fetchall()
        return {
            "phase": 24,
            "developer_clients": int(clients["total"] or 0),
            "active_clients": int(clients["active"] or 0),
            "requests": {
                row["surface"]: {
                    "total": int(row["total"]),
                    "average_latency_ms": round(
                        float(row["average_latency"] or 0), 2
                    ),
                }
                for row in requests
            },
            "capabilities": {
                "openapi_3_1": True,
                "mcp_json_rpc": True,
                "cursor_pagination": True,
                "idempotency_keys": True,
                "service_credentials": True,
                "python_sdk": True,
                "typescript_sdk": True,
                "cli": True,
            },
        }
