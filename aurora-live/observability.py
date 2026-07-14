from __future__ import annotations

import json
import os
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def log_event(event: str, level: str = "info", **fields) -> None:
    payload = {"time": utc_now(), "level": level, "event": event}
    payload.update({key: value for key, value in fields.items() if value is not None})
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str), flush=True)


class Metrics:
    def __init__(self):
        self.lock = threading.Lock()
        self.counters = defaultdict(float)
        self.histograms = defaultdict(lambda: {"count": 0.0, "sum": 0.0})
        self.gauges = defaultdict(float)

    @staticmethod
    def _key(name: str, labels: dict | None = None):
        return name, tuple(sorted((labels or {}).items()))

    def inc(self, name: str, value: float = 1.0, **labels):
        with self.lock:
            self.counters[self._key(name, labels)] += value

    def observe(self, name: str, value: float, **labels):
        with self.lock:
            item = self.histograms[self._key(name, labels)]
            item["count"] += 1
            item["sum"] += float(value)

    def set(self, name: str, value: float, **labels):
        with self.lock:
            self.gauges[self._key(name, labels)] = float(value)

    @staticmethod
    def _labels(labels):
        if not labels:
            return ""
        escaped = [f'{key}="{str(value).replace(chr(92), chr(92)+chr(92)).replace(chr(34), chr(92)+chr(34))}"' for key, value in labels]
        return "{" + ",".join(escaped) + "}"

    def render(self) -> bytes:
        lines = []
        with self.lock:
            for (name, labels), value in sorted(self.counters.items()):
                lines.append(f"{name}{self._labels(labels)} {value:g}")
            for (name, labels), item in sorted(self.histograms.items()):
                suffix = self._labels(labels)
                lines.append(f"{name}_count{suffix} {item['count']:g}")
                lines.append(f"{name}_sum{suffix} {item['sum']:g}")
            for (name, labels), value in sorted(self.gauges.items()):
                lines.append(f"{name}{self._labels(labels)} {value:g}")
        return (("\n".join(lines) + "\n") if lines else "").encode()


METRICS = Metrics()


def timed_request(method: str, path: str):
    started = time.monotonic()

    def complete(status: int):
        elapsed = time.monotonic() - started
        route = path if path in {"/api/platform/live", "/api/platform/ready", "/api/platform/health", "/api/platform/metrics"} else "other"
        METRICS.inc("aurora_http_requests_total", method=method, route=route, status=str(status))
        METRICS.observe("aurora_http_request_duration_seconds", elapsed, method=method, route=route)
        return elapsed

    return complete


def metrics_enabled() -> bool:
    return os.getenv("AURORA_METRICS_ENABLED", "1") == "1"
