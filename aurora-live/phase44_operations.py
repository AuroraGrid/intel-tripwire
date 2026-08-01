from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _is_postgres(target: str) -> bool:
    return target.startswith(("postgresql://", "postgres://"))


class OperationsHistoryStore:
    """Durable uptime/health snapshots for long-running operational proof."""

    def __init__(self, target: str = ":memory:") -> None:
        self.target = str(target)
        self.postgres = _is_postgres(self.target)
        self._lock = threading.RLock()
        if self.postgres:
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc:
                raise RuntimeError("psycopg is required for PostgreSQL operations history") from exc
            self._connection = psycopg.connect(self.target, row_factory=dict_row)
            self._p = "%s"
        else:
            if self.target != ":memory:":
                Path(self.target).parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(self.target, check_same_thread=False)
            self._connection.row_factory = sqlite3.Row
            self._p = "?"
        self._initialize()

    def _initialize(self) -> None:
        pk = "sample_id BIGSERIAL PRIMARY KEY" if self.postgres else "sample_id INTEGER PRIMARY KEY AUTOINCREMENT"
        with self._lock:
            self._connection.execute(
                f"""CREATE TABLE IF NOT EXISTS ops_samples (
                    {pk}, sampled_at TEXT NOT NULL, status TEXT NOT NULL,
                    uptime_ok INTEGER NOT NULL, redundancy_ok INTEGER NOT NULL,
                    detail_json TEXT NOT NULL
                )"""
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_ops_samples_time ON ops_samples(sampled_at DESC)"
            )
            self._connection.commit()

    @staticmethod
    def _dict(row: Any) -> dict[str, Any]:
        return dict(row) if row is not None else {}

    def record(self, *, status: str, uptime_ok: bool, redundancy_ok: bool, detail: dict[str, Any]) -> dict[str, Any]:
        values = (
            _now(),
            status,
            1 if uptime_ok else 0,
            1 if redundancy_ok else 0,
            json.dumps(detail, sort_keys=True, separators=(",", ":")),
        )
        with self._lock:
            self._connection.execute(
                f"INSERT INTO ops_samples(sampled_at,status,uptime_ok,redundancy_ok,detail_json) VALUES ({','.join([self._p]*5)})",
                values,
            )
            self._connection.commit()
        return {
            "sampled_at": values[0],
            "status": status,
            "uptime_ok": uptime_ok,
            "redundancy_ok": redundancy_ok,
            "detail": detail,
        }

    def history(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 1000))
        with self._lock:
            rows = self._connection.execute(
                f"SELECT * FROM ops_samples ORDER BY sample_id DESC LIMIT {self._p}",
                (limit,),
            ).fetchall()
        output = []
        for row in rows:
            item = self._dict(row)
            item["uptime_ok"] = bool(item.get("uptime_ok"))
            item["redundancy_ok"] = bool(item.get("redundancy_ok"))
            try:
                item["detail"] = json.loads(item.pop("detail_json", "{}"))
            except json.JSONDecodeError:
                item["detail"] = {}
            output.append(item)
        return output

    def summary(self) -> dict[str, Any]:
        rows = self.history(limit=500)
        if not rows:
            return {
                "samples": 0,
                "uptime_ratio": None,
                "redundancy_ratio": None,
                "status": "NO_SAMPLES",
                "generated_at": _now(),
            }
        uptime = sum(1 for row in rows if row["uptime_ok"]) / len(rows)
        redundancy = sum(1 for row in rows if row["redundancy_ok"]) / len(rows)
        return {
            "samples": len(rows),
            "uptime_ratio": round(uptime, 4),
            "redundancy_ratio": round(redundancy, 4),
            "status": "HEALTHY" if uptime >= 0.99 and redundancy >= 0.95 else "DEGRADED" if uptime >= 0.9 else "POOR",
            "latest": rows[0],
            "generated_at": _now(),
        }


def evaluate_redundancy(*, primary_ok: bool, secondary_ok: bool | None = None) -> dict[str, Any]:
    """Evaluate verified multi-host redundancy.

    A single primary host is never counted as verified redundancy. Dual mode
    requires both primary and secondary heartbeats to succeed.
    """
    if secondary_ok is None:
        return {
            "mode": "single",
            "ok": False,
            "primary_ok": primary_ok,
            "secondary_ok": None,
            "verified": False,
            "note": "Single host only; secondary heartbeat not configured. Not verified redundancy.",
        }
    verified = bool(primary_ok and secondary_ok)
    return {
        "mode": "dual",
        "ok": verified,
        "primary_ok": primary_ok,
        "secondary_ok": secondary_ok,
        "verified": verified,
        "note": (
            "Verified dual-host redundancy."
            if verified
            else "Dual heartbeat configured but one or both hosts are not healthy."
        ),
    }
