from __future__ import annotations

import hashlib
import hmac
import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone

from storage import now


def iso_after(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=max(0, int(seconds)))).isoformat().replace("+00:00", "Z")


class DeliveryQueue:
    def __init__(self, operations, max_attempts=5, base_backoff=30, max_backoff=3600):
        self.operations = operations
        self.store = operations.store
        self.max_attempts = max(1, int(max_attempts))
        self.base_backoff = max(1, int(base_backoff))
        self.max_backoff = max(self.base_backoff, int(max_backoff))
        self.init()

    def init(self):
        columns = self.store.database.column_names("deliveries")
        with self.store.db() as connection:
            if "next_attempt_at" not in columns:
                connection.execute("ALTER TABLE deliveries ADD COLUMN next_attempt_at TEXT")
            if "dead_lettered_at" not in columns:
                connection.execute("ALTER TABLE deliveries ADD COLUMN dead_lettered_at TEXT")
            connection.execute("UPDATE deliveries SET next_attempt_at=updated_at WHERE next_attempt_at IS NULL AND status IN ('pending','failed')")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_deliveries_due ON deliveries(status,next_attempt_at)")

    def due(self, limit=100):
        stamp = now()
        sql = """SELECT d.*,w.url,w.name webhook_name,a.user_id,a.watchlist_id,a.incident_id,
        i.title incident_title,i.severity,i.confidence,i.category,i.status AS incident_status,i.grade,i.action
        FROM deliveries d
        JOIN webhooks w ON w.id=d.webhook_id
        JOIN alerts a ON a.id=d.alert_id
        JOIN incidents i ON i.id=a.incident_id
        WHERE d.status IN ('pending','failed') AND d.attempts<? AND w.active=1
        AND (d.next_attempt_at IS NULL OR d.next_attempt_at<=?)
        ORDER BY COALESCE(d.next_attempt_at,d.updated_at),d.id LIMIT ?"""
        with self.store.db() as connection:
            return [dict(row) for row in connection.execute(sql, (self.max_attempts, stamp, max(1, min(500, int(limit))))).fetchall()]

    def record(self, delivery_id, success, error=""):
        stamp = now()
        with self.store.db() as connection:
            row = connection.execute("SELECT attempts FROM deliveries WHERE id=?", (delivery_id,)).fetchone()
            if not row:
                raise KeyError("delivery not found")
            attempts = int(row["attempts"]) + 1
            if success:
                connection.execute("""UPDATE deliveries SET status='delivered',attempts=?,last_error=NULL,
                    delivered_at=?,updated_at=?,next_attempt_at=NULL,dead_lettered_at=NULL WHERE id=?""",
                    (attempts, stamp, stamp, delivery_id))
                return "delivered"
            dead = attempts >= self.max_attempts
            delay = min(self.max_backoff, self.base_backoff * (2 ** max(0, attempts - 1)))
            connection.execute("""UPDATE deliveries SET status=?,attempts=?,last_error=?,delivered_at=NULL,
                updated_at=?,next_attempt_at=?,dead_lettered_at=? WHERE id=?""",
                ("dead" if dead else "failed", attempts, str(error)[:500], stamp,
                 None if dead else iso_after(delay), stamp if dead else None, delivery_id))
            return "dead" if dead else "failed"

    def deliver_due(self, opener=urllib.request.urlopen, timeout=8, limit=100):
        rows = self.due(limit)
        counts = {"attempted": len(rows), "delivered": 0, "failed": 0, "dead": 0}
        secret = os.getenv("AURORA_WEBHOOK_SECRET", "")
        for row in rows:
            payload = {"type": "aurora.alert", "alert": {
                "id": row["alert_id"], "watchlist_id": row["watchlist_id"], "incident_id": row["incident_id"],
                "incident_title": row["incident_title"], "category": row["category"], "severity": row["severity"],
                "confidence": row["confidence"], "status": row["incident_status"], "grade": row["grade"], "action": row["action"]}}
            body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
            headers = {"Content-Type": "application/json", "User-Agent": "AuroraLiveWorker/0.4"}
            if secret:
                headers["X-Aurora-Signature"] = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
            request = urllib.request.Request(row["url"], data=body, headers=headers, method="POST")
            try:
                with opener(request, timeout=timeout) as response:
                    status = int(getattr(response, "status", 200))
                if 200 <= status < 300:
                    outcome = self.record(row["id"], True)
                else:
                    outcome = self.record(row["id"], False, f"HTTP {status}")
            except Exception as exc:
                outcome = self.record(row["id"], False, str(exc))
            counts[outcome] += 1
        return counts
