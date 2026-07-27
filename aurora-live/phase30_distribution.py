from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from storage import now, sid

CLASSIFICATIONS = {"PUBLIC": 0, "INTERNAL": 1, "CONFIDENTIAL": 2, "RESTRICTED": 3}
DELIVERY_STATUSES = {"QUEUED", "SENT", "DELIVERED", "FAILED", "CANCELLED"}
REFERENCE_KEYS = {"artifact", "checksum", "receipt", "report", "run_id", "sha256", "url"}
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,127}$")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _load(value: Any, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except (TypeError, json.JSONDecodeError):
        return default


def _text(value: Any, field: str, maximum: int = 300) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} required")
    if len(text) > maximum:
        raise ValueError(f"{field} exceeds {maximum} characters")
    return text


def _classification(value: Any) -> str:
    result = str(value or "PUBLIC").upper()
    if result not in CLASSIFICATIONS:
        raise ValueError("invalid classification")
    return result


def _evidence(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not any(str(value.get(key) or "").strip() for key in REFERENCE_KEYS):
        raise ValueError("evidence must contain a durable reference")
    return value


class DistributionHub:
    """Workspace-scoped deterministic intelligence packaging and delivery ledger."""

    def __init__(self, store):
        self.store = store
        self.init()

    def init(self) -> None:
        with self.store.db() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS distribution_channels(
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    channel_key TEXT NOT NULL,
                    name TEXT NOT NULL,
                    channel_type TEXT NOT NULL,
                    clearance TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    active INTEGER NOT NULL,
                    metadata TEXT NOT NULL,
                    actor_user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(workspace_id,channel_key)
                );
                CREATE TABLE IF NOT EXISTS distribution_packages(
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    package_key TEXT NOT NULL,
                    classification TEXT NOT NULL,
                    title TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    manifest TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    actor_user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(workspace_id,package_key,sha256)
                );
                CREATE TABLE IF NOT EXISTS distribution_deliveries(
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    package_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    actor_user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(workspace_id,idempotency_key)
                );
                CREATE INDEX IF NOT EXISTS idx_distribution_channels
                    ON distribution_channels(workspace_id,active,channel_key);
                CREATE INDEX IF NOT EXISTS idx_distribution_packages
                    ON distribution_packages(workspace_id,created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_distribution_deliveries
                    ON distribution_deliveries(workspace_id,package_id,created_at DESC);
                """
            )

    @staticmethod
    def _workspace(actor: dict[str, Any]) -> str:
        return str(actor["workspace_id"])

    @staticmethod
    def _actor(actor: dict[str, Any]) -> str:
        return str(actor.get("id") or "system")

    def _admin(self, actor: dict[str, Any]) -> None:
        self.store.identity.require(actor, "admin")

    def upsert_channel(self, actor: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        self._admin(actor)
        workspace = self._workspace(actor)
        key = _text(payload.get("channel_key"), "channel_key", 128).lower()
        if not KEY_RE.fullmatch(key):
            raise ValueError("invalid channel_key")
        clearance = _classification(payload.get("clearance"))
        stamp = now()
        channel_id = sid("distribution-channel", workspace, key)
        row = (
            channel_id,
            workspace,
            key,
            _text(payload.get("name"), "name"),
            _text(payload.get("channel_type"), "channel_type", 64).upper(),
            clearance,
            _text(payload.get("destination"), "destination", 500),
            int(bool(payload.get("active", True))),
            _json(payload.get("metadata") or {}),
            self._actor(actor),
            stamp,
            stamp,
        )
        with self.store.db() as connection:
            connection.execute(
                """INSERT INTO distribution_channels(
                id,workspace_id,channel_key,name,channel_type,clearance,destination,
                active,metadata,actor_user_id,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(workspace_id,channel_key) DO UPDATE SET
                name=excluded.name,channel_type=excluded.channel_type,
                clearance=excluded.clearance,destination=excluded.destination,
                active=excluded.active,metadata=excluded.metadata,
                actor_user_id=excluded.actor_user_id,updated_at=excluded.updated_at""",
                row,
            )
        self.store.identity.audit(workspace, self._actor(actor), "distribution.channel_upserted", "distribution_channel", channel_id)
        return self.channel(actor, channel_id)

    def channel(self, actor: dict[str, Any], channel_id: str) -> dict[str, Any]:
        with self.store.db() as connection:
            row = connection.execute(
                "SELECT * FROM distribution_channels WHERE workspace_id=? AND id=?",
                (self._workspace(actor), channel_id),
            ).fetchone()
        if not row:
            raise KeyError("channel not found")
        item = dict(row)
        item["active"] = bool(item["active"])
        item["metadata"] = _load(item["metadata"], {})
        return item

    def channels(self, actor: dict[str, Any], active: str = "", limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT id FROM distribution_channels WHERE workspace_id=?"
        args: list[Any] = [self._workspace(actor)]
        if active != "":
            sql += " AND active=?"
            args.append(int(str(active).lower() in {"1", "true", "yes"}))
        sql += " ORDER BY channel_key LIMIT ?"
        args.append(max(1, min(200, int(limit))))
        with self.store.db() as connection:
            rows = connection.execute(sql, args).fetchall()
        return [self.channel(actor, row["id"]) for row in rows]

    def create_package(self, actor: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        self.store.identity.require(actor, "write")
        workspace = self._workspace(actor)
        package_key = _text(payload.get("package_key"), "package_key", 128).lower()
        if not KEY_RE.fullmatch(package_key):
            raise ValueError("invalid package_key")
        classification = _classification(payload.get("classification"))
        body = payload.get("payload")
        if not isinstance(body, dict) or not body:
            raise ValueError("payload must be a non-empty object")
        canonical = _json(body)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        package_id = sid("distribution-package", workspace, package_key, digest)
        manifest = {
            "algorithm": "sha256",
            "sha256": digest,
            "canonical_json": True,
            "classification": classification,
            "payload_bytes": len(canonical.encode("utf-8")),
        }
        stamp = now()
        with self.store.db() as connection:
            connection.execute(
                """INSERT INTO distribution_packages(
                id,workspace_id,package_key,classification,title,payload,manifest,
                sha256,actor_user_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(workspace_id,package_key,sha256) DO NOTHING""",
                (
                    package_id,
                    workspace,
                    package_key,
                    classification,
                    _text(payload.get("title"), "title"),
                    canonical,
                    _json(manifest),
                    digest,
                    self._actor(actor),
                    stamp,
                ),
            )
        self.store.identity.audit(workspace, self._actor(actor), "distribution.package_created", "distribution_package", package_id)
        return self.package(actor, package_id)

    def package(self, actor: dict[str, Any], package_id: str) -> dict[str, Any]:
        with self.store.db() as connection:
            row = connection.execute(
                "SELECT * FROM distribution_packages WHERE workspace_id=? AND id=?",
                (self._workspace(actor), package_id),
            ).fetchone()
        if not row:
            raise KeyError("package not found")
        item = dict(row)
        item["payload"] = _load(item["payload"], {})
        item["manifest"] = _load(item["manifest"], {})
        return item

    def packages(self, actor: dict[str, Any], classification: str = "", limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT id FROM distribution_packages WHERE workspace_id=?"
        args: list[Any] = [self._workspace(actor)]
        if classification:
            sql += " AND classification=?"
            args.append(_classification(classification))
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(max(1, min(200, int(limit))))
        with self.store.db() as connection:
            rows = connection.execute(sql, args).fetchall()
        return [self.package(actor, row["id"]) for row in rows]

    def queue_delivery(self, actor: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        self.store.identity.require(actor, "write")
        workspace = self._workspace(actor)
        package = self.package(actor, _text(payload.get("package_id"), "package_id"))
        channel = self.channel(actor, _text(payload.get("channel_id"), "channel_id"))
        if not channel["active"]:
            raise ValueError("channel is inactive")
        if CLASSIFICATIONS[package["classification"]] > CLASSIFICATIONS[channel["clearance"]]:
            raise PermissionError("channel clearance is below package classification")
        idempotency_key = _text(payload.get("idempotency_key"), "idempotency_key", 200)
        stamp = now()
        delivery_id = sid("distribution-delivery", workspace, idempotency_key)
        with self.store.db() as connection:
            connection.execute(
                """INSERT INTO distribution_deliveries(
                id,workspace_id,package_id,channel_id,idempotency_key,status,evidence,
                actor_user_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(workspace_id,idempotency_key) DO NOTHING""",
                (
                    delivery_id,
                    workspace,
                    package["id"],
                    channel["id"],
                    idempotency_key,
                    "QUEUED",
                    "{}",
                    self._actor(actor),
                    stamp,
                    stamp,
                ),
            )
        self.store.identity.audit(workspace, self._actor(actor), "distribution.delivery_queued", "distribution_delivery", delivery_id)
        return self.delivery(actor, delivery_id)

    def record_delivery(self, actor: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        self._admin(actor)
        delivery_id = _text(payload.get("delivery_id"), "delivery_id")
        current = self.delivery(actor, delivery_id)
        status = str(payload.get("status") or "").upper()
        if status not in DELIVERY_STATUSES - {"QUEUED"}:
            raise ValueError("invalid delivery status")
        evidence = _evidence(payload.get("evidence"))
        with self.store.db() as connection:
            connection.execute(
                """UPDATE distribution_deliveries SET status=?,evidence=?,
                actor_user_id=?,updated_at=? WHERE workspace_id=? AND id=?""",
                (status, _json(evidence), self._actor(actor), now(), self._workspace(actor), current["id"]),
            )
        self.store.identity.audit(self._workspace(actor), self._actor(actor), "distribution.delivery_recorded", "distribution_delivery", current["id"], metadata={"status": status})
        return self.delivery(actor, current["id"])

    def delivery(self, actor: dict[str, Any], delivery_id: str) -> dict[str, Any]:
        with self.store.db() as connection:
            row = connection.execute(
                "SELECT * FROM distribution_deliveries WHERE workspace_id=? AND id=?",
                (self._workspace(actor), delivery_id),
            ).fetchone()
        if not row:
            raise KeyError("delivery not found")
        item = dict(row)
        item["evidence"] = _load(item["evidence"], {})
        return item

    def deliveries(self, actor: dict[str, Any], package_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT id FROM distribution_deliveries WHERE workspace_id=?"
        args: list[Any] = [self._workspace(actor)]
        if package_id:
            sql += " AND package_id=?"
            args.append(package_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(max(1, min(200, int(limit))))
        with self.store.db() as connection:
            rows = connection.execute(sql, args).fetchall()
        return [self.delivery(actor, row["id"]) for row in rows]
