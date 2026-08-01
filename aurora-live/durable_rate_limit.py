"""Shared platform rate limiter backed by the application database."""
from __future__ import annotations

import threading
import time
from typing import Any


class DurableRateLimiter:
    """Fixed-window limiter suitable for multi-worker deployments.

    Falls back to process-local memory when no store is provided (tests).
    """

    def __init__(self, store=None, max_entries: int = 10000) -> None:
        self.store = store
        self.max_entries = max(100, int(max_entries))
        self.data: dict[str, tuple[float, int, float]] = {}
        self.lock = threading.Lock()
        if self.store is not None:
            self._init_schema()

    def _init_schema(self) -> None:
        with self.store.db() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS platform_rate_buckets(
                    bucket_key TEXT PRIMARY KEY,
                    window_started_at REAL NOT NULL,
                    hit_count INTEGER NOT NULL,
                    updated_at REAL NOT NULL
                )"""
            )

    def check(self, key: str, limit: int, window: int) -> list[tuple[str, str]] | None:
        if limit <= 0:
            return None
        if self.store is None:
            return self._memory_check(key, limit, window)
        return self._db_check(key, limit, window)

    def _memory_check(self, key: str, limit: int, window: int) -> list[tuple[str, str]] | None:
        from platform_wsgi import HTTPError

        stamp = time.monotonic()
        with self.lock:
            started, count, _ = self.data.get(key, (stamp, 0, stamp))
            if stamp - started >= window:
                started, count = stamp, 0
            count += 1
            self.data[key] = (started, count, stamp)
            if len(self.data) > self.max_entries:
                for old, _ in sorted(self.data.items(), key=lambda x: x[1][2])[: len(self.data) - self.max_entries]:
                    self.data.pop(old, None)
            retry = max(1, int(window - (stamp - started)))
            if count > limit:
                raise HTTPError(
                    429,
                    "rate_limited",
                    "request rate limit exceeded",
                    [("Retry-After", str(retry)), ("X-RateLimit-Limit", str(limit)), ("X-RateLimit-Remaining", "0")],
                )
            return [
                ("X-RateLimit-Limit", str(limit)),
                ("X-RateLimit-Remaining", str(max(0, limit - count))),
            ]

    def _db_check(self, key: str, limit: int, window: int) -> list[tuple[str, str]] | None:
        from platform_wsgi import HTTPError

        now = time.time()
        with self.lock:
            with self.store.db() as connection:
                row = connection.execute(
                    "SELECT window_started_at, hit_count FROM platform_rate_buckets WHERE bucket_key=?",
                    (key,),
                ).fetchone()
                if row is None:
                    connection.execute(
                        "INSERT INTO platform_rate_buckets(bucket_key,window_started_at,hit_count,updated_at) VALUES(?,?,?,?)",
                        (key, now, 1, now),
                    )
                    return [
                        ("X-RateLimit-Limit", str(limit)),
                        ("X-RateLimit-Remaining", str(max(0, limit - 1))),
                    ]
                started = float(row["window_started_at"] if isinstance(row, dict) else row[0])
                count = int(row["hit_count"] if isinstance(row, dict) else row[1])
                if now - started >= window:
                    count = 1
                    started = now
                    connection.execute(
                        "UPDATE platform_rate_buckets SET window_started_at=?, hit_count=?, updated_at=? WHERE bucket_key=?",
                        (started, count, now, key),
                    )
                else:
                    count += 1
                    connection.execute(
                        "UPDATE platform_rate_buckets SET hit_count=?, updated_at=? WHERE bucket_key=?",
                        (count, now, key),
                    )
                retry = max(1, int(window - (now - started)))
                if count > limit:
                    raise HTTPError(
                        429,
                        "rate_limited",
                        "request rate limit exceeded",
                        [
                            ("Retry-After", str(retry)),
                            ("X-RateLimit-Limit", str(limit)),
                            ("X-RateLimit-Remaining", "0"),
                        ],
                    )
                return [
                    ("X-RateLimit-Limit", str(limit)),
                    ("X-RateLimit-Remaining", str(max(0, limit - count))),
                ]
