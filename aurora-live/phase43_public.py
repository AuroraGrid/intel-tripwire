from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def public_mode_enabled() -> bool:
    return str(os.getenv("AURORA_PUBLIC_MODE", "1")).strip().lower() in {"1", "true", "yes", "on"}


def public_config() -> dict[str, Any]:
    return {
        "public_mode": public_mode_enabled(),
        "no_paywall": True,
        "abuse_controls": True,
        "pwa_shell": True,
        "notification_scaffolding": bool(str(os.getenv("AURORA_VAPID_PUBLIC_KEY") or "").strip()),
        "rate_limit_per_minute": int(os.getenv("AURORA_PUBLIC_RATE_LIMIT", "120")),
        "cache_control_product": "public, max-age=30",
        "cache_control_observations": "public, max-age=5, must-revalidate",
        "credentials_never_returned": True,
        "generated_at": _now(),
    }


@dataclass
class RateLimitDecision:
    allowed: bool
    remaining: int
    reset_seconds: int
    limit: int

    def headers(self) -> list[tuple[str, str]]:
        return [
            ("X-RateLimit-Limit", str(self.limit)),
            ("X-RateLimit-Remaining", str(max(0, self.remaining))),
            ("X-RateLimit-Reset", str(max(0, self.reset_seconds))),
        ]


class AbuseLimiter:
    """In-memory sliding-window limiter for public endpoints."""

    def __init__(self, limit: int | None = None, window_seconds: int = 60) -> None:
        self.limit = max(1, int(limit or os.getenv("AURORA_PUBLIC_RATE_LIMIT", "120")))
        self.window_seconds = max(1, int(window_seconds))
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.RLock()

    def check(self, key: str) -> RateLimitDecision:
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
            reset = int(self.window_seconds)
            return RateLimitDecision(True, remaining, reset, self.limit)


class NotificationStore:
    """Scaffolding for browser push subscriptions. Delivery requires VAPID keys."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subscriptions: list[dict[str, Any]] = []

    def add(self, payload: dict[str, Any]) -> dict[str, Any]:
        endpoint = str(payload.get("endpoint") or "").strip()
        if not endpoint.startswith("https://"):
            raise ValueError("endpoint must be an https URL")
        item = {
            "endpoint": endpoint,
            "keys": payload.get("keys") if isinstance(payload.get("keys"), dict) else {},
            "created_at": _now(),
            "delivery_enabled": bool(str(os.getenv("AURORA_VAPID_PUBLIC_KEY") or "").strip()),
        }
        with self._lock:
            self._subscriptions = [row for row in self._subscriptions if row["endpoint"] != endpoint]
            self._subscriptions.append(item)
        return {
            "stored": True,
            "delivery_enabled": item["delivery_enabled"],
            "count": len(self._subscriptions),
            "note": "Subscription stored; push delivery remains disabled without VAPID configuration.",
        }

    def count(self) -> int:
        with self._lock:
            return len(self._subscriptions)


def cache_headers_for(path: str) -> list[tuple[str, str]]:
    if path.startswith("/api/public/product") or path.startswith("/.well-known/"):
        return [("Cache-Control", public_config()["cache_control_product"])]
    if path.startswith("/api/public/"):
        return [("Cache-Control", public_config()["cache_control_observations"])]
    if path.startswith("/static/"):
        return [("Cache-Control", "public, max-age=300")]
    return [("Cache-Control", "no-store")]
