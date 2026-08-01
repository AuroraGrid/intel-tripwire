"""Durable (shared) public rate limiting and push subscription storage."""
from __future__ import annotations

import json
import os
import threading
import time
from collections import defaultdict, deque
from typing import Any

from phase43_public import RateLimitDecision, _now


class DurableAbuseLimiter:
    """Fixed-window rate limiter shared via the platform database when available."""

    def __init__(self, store=None, limit: int | None = None, window_seconds: int = 60) -> None:
        self.store = store
        self.limit = max(1, int(limit or os.getenv("AURORA_PUBLIC_RATE_LIMIT", "120")))
        self.window_seconds = max(1, int(window_seconds))
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.RLock()
        if self.store is not None:
            self._init_schema()

    def _init_schema(self) -> None:
        with self.store.db() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS public_rate_buckets(
                    bucket_key TEXT PRIMARY KEY,
                    window_started_at REAL NOT NULL,
                    hit_count INTEGER NOT NULL,
                    updated_at REAL NOT NULL
                )"""
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_public_rate_updated ON public_rate_buckets(updated_at)"
            )

    def check(self, key: str) -> RateLimitDecision:
        if self.store is None:
            return self._memory_check(key)
        return self._db_check(key)

    def _memory_check(self, key: str) -> RateLimitDecision:
        now = time.monotonic()
        with self._lock:
            bucket = self._events[key]
            cutoff = now - self.window_seconds
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self.limit:
                reset = int(max(1, self.window_seconds - (now - bucket[0])))
                return RateLimitDecision(False, 0, reset, self.limit)
            bucket.append(now)
            remaining = self.limit - len(bucket)
            return RateLimitDecision(True, remaining, self.window_seconds, self.limit)

    def _db_check(self, key: str) -> RateLimitDecision:
        now = time.time()
        with self._lock:
            with self.store.db() as connection:
                row = connection.execute(
                    "SELECT window_started_at, hit_count FROM public_rate_buckets WHERE bucket_key=?",
                    (key,),
                ).fetchone()
                if row is None:
                    connection.execute(
                        "INSERT INTO public_rate_buckets(bucket_key,window_started_at,hit_count,updated_at) VALUES(?,?,?,?)",
                        (key, now, 1, now),
                    )
                    return RateLimitDecision(True, self.limit - 1, self.window_seconds, self.limit)
                started = float(row["window_started_at"] if isinstance(row, dict) else row[0])
                count = int(row["hit_count"] if isinstance(row, dict) else row[1])
                if now - started >= self.window_seconds:
                    connection.execute(
                        "UPDATE public_rate_buckets SET window_started_at=?, hit_count=?, updated_at=? WHERE bucket_key=?",
                        (now, 1, now, key),
                    )
                    return RateLimitDecision(True, self.limit - 1, self.window_seconds, self.limit)
                if count >= self.limit:
                    reset = int(max(1, self.window_seconds - (now - started)))
                    return RateLimitDecision(False, 0, reset, self.limit)
                connection.execute(
                    "UPDATE public_rate_buckets SET hit_count=?, updated_at=? WHERE bucket_key=?",
                    (count + 1, now, key),
                )
                remaining = self.limit - (count + 1)
                reset = int(max(1, self.window_seconds - (now - started)))
                return RateLimitDecision(True, remaining, reset, self.limit)


class DurableNotificationStore:
    """Push subscription store persisted in the platform database."""

    def __init__(self, store=None) -> None:
        self.store = store
        self._lock = threading.RLock()
        self._subscriptions: list[dict[str, Any]] = []
        if self.store is not None:
            self._init_schema()

    def _init_schema(self) -> None:
        with self.store.db() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS push_subscriptions(
                    endpoint TEXT PRIMARY KEY,
                    keys_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    delivery_enabled INTEGER NOT NULL
                )"""
            )

    def add(self, payload: dict[str, Any]) -> dict[str, Any]:
        from webhook_security import resolve_public_https_url

        endpoint = str(payload.get("endpoint") or "").strip()
        # Push endpoints are HTTPS URLs controlled by browsers; apply same public-host policy.
        resolve_public_https_url(endpoint)
        item = {
            "endpoint": endpoint,
            "keys": payload.get("keys") if isinstance(payload.get("keys"), dict) else {},
            "created_at": _now(),
            "delivery_enabled": bool(str(os.getenv("AURORA_VAPID_PUBLIC_KEY") or "").strip()),
        }
        if self.store is None:
            with self._lock:
                self._subscriptions = [row for row in self._subscriptions if row["endpoint"] != endpoint]
                self._subscriptions.append(item)
                count = len(self._subscriptions)
        else:
            with self._lock:
                with self.store.db() as connection:
                    connection.execute(
                        """INSERT INTO push_subscriptions(endpoint,keys_json,created_at,delivery_enabled)
                        VALUES(?,?,?,?)
                        ON CONFLICT(endpoint) DO UPDATE SET
                        keys_json=excluded.keys_json,
                        created_at=excluded.created_at,
                        delivery_enabled=excluded.delivery_enabled""",
                        (
                            endpoint,
                            json.dumps(item["keys"], sort_keys=True, separators=(",", ":")),
                            item["created_at"],
                            1 if item["delivery_enabled"] else 0,
                        ),
                    )
                    row = connection.execute("SELECT COUNT(*) AS c FROM push_subscriptions").fetchone()
                    count = int(row["c"] if isinstance(row, dict) else row[0])
        return {
            "stored": True,
            "delivery_enabled": item["delivery_enabled"],
            "count": count,
            "note": "Subscription stored; push delivery remains disabled without VAPID configuration.",
        }

    def count(self) -> int:
        if self.store is None:
            with self._lock:
                return len(self._subscriptions)
        with self.store.db() as connection:
            row = connection.execute("SELECT COUNT(*) AS c FROM push_subscriptions").fetchone()
            return int(row["c"] if isinstance(row, dict) else row[0])
