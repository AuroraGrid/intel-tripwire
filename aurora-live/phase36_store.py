from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from phase35_ingestion import IngestionResult


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _is_postgres(target: str) -> bool:
    return target.startswith(("postgresql://", "postgres://"))


class OperationalStore:
    """Shared SQLite/PostgreSQL contract for ingestion, scheduling and health."""

    def __init__(self, target: str = ":memory:") -> None:
        self.target = str(target)
        self.postgres = _is_postgres(self.target)
        self._lock = threading.RLock()
        if self.postgres:
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc:
                raise RuntimeError("psycopg is required for PostgreSQL operational storage") from exc
            self._connection = psycopg.connect(self.target, row_factory=dict_row)
            self._p = "%s"
        else:
            if self.target != ":memory:":
                Path(self.target).parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(self.target, check_same_thread=False)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._p = "?"
        self._initialize()

    def _initialize(self) -> None:
        serial = "BIGSERIAL" if self.postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
        run_pk = f"run_id {serial} PRIMARY KEY" if self.postgres else f"run_id {serial}"
        observation_pk = f"observation_id {serial} PRIMARY KEY" if self.postgres else f"observation_id {serial}"
        tick_pk = f"tick_id {serial} PRIMARY KEY" if self.postgres else f"tick_id {serial}"
        statements = [
            f"""CREATE TABLE IF NOT EXISTS ingestion_runs (
                {run_pk}, adapter TEXT NOT NULL, started_at TEXT NOT NULL, completed_at TEXT,
                status TEXT NOT NULL, discovered INTEGER NOT NULL DEFAULT 0,
                succeeded INTEGER NOT NULL DEFAULT 0, failed INTEGER NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT '')""",
            f"""CREATE TABLE IF NOT EXISTS imagery_observations (
                {observation_pk}, run_id BIGINT NOT NULL REFERENCES ingestion_runs(run_id),
                adapter TEXT NOT NULL, external_id TEXT NOT NULL, source_id TEXT NOT NULL,
                state TEXT NOT NULL, captured_at TEXT NOT NULL, observed_at TEXT NOT NULL,
                content_sha256 TEXT NOT NULL, content_type TEXT NOT NULL, byte_length BIGINT NOT NULL,
                width INTEGER NOT NULL, height INTEGER NOT NULL, error TEXT NOT NULL DEFAULT '',
                raw_metadata TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS provider_health (
                adapter TEXT PRIMARY KEY, circuit_state TEXT NOT NULL,
                consecutive_failures INTEGER NOT NULL, last_status TEXT NOT NULL,
                last_run_id BIGINT, last_attempt_at TEXT NOT NULL, last_success_at TEXT NOT NULL,
                next_due_at TEXT NOT NULL, next_attempt_at TEXT NOT NULL,
                last_error TEXT NOT NULL, telemetry_json TEXT NOT NULL, updated_at TEXT NOT NULL)""",
            f"""CREATE TABLE IF NOT EXISTS scheduler_ticks (
                {tick_pk}, started_at TEXT NOT NULL, completed_at TEXT NOT NULL,
                requested INTEGER NOT NULL, executed INTEGER NOT NULL, skipped INTEGER NOT NULL,
                successful INTEGER NOT NULL, failed INTEGER NOT NULL, details_json TEXT NOT NULL)""",
            "CREATE INDEX IF NOT EXISTS idx_observations_source_time ON imagery_observations(source_id, observed_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_observations_adapter_time ON imagery_observations(adapter, observed_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_runs_adapter_time ON ingestion_runs(adapter, started_at DESC)",
        ]
        with self._lock:
            cursor = self._connection.cursor()
            for statement in statements:
                cursor.execute(statement)
            self._connection.commit()

    @staticmethod
    def _dict(row: Any) -> dict[str, Any]:
        return dict(row) if row is not None else {}

    def start_run(self, adapter: str) -> int:
        with self._lock:
            cursor = self._connection.cursor()
            if self.postgres:
                cursor.execute(
                    "INSERT INTO ingestion_runs(adapter, started_at, status) VALUES (%s, %s, 'RUNNING') RETURNING run_id",
                    (adapter, _now()),
                )
                run_id = int(cursor.fetchone()["run_id"])
            else:
                cursor.execute(
                    "INSERT INTO ingestion_runs(adapter, started_at, status) VALUES (?, ?, 'RUNNING')",
                    (adapter, _now()),
                )
                run_id = int(cursor.lastrowid)
            self._connection.commit()
            return run_id

    def finish_run(self, run_id: int, *, discovered: int, succeeded: int, failed: int, error: str = "") -> None:
        status = "SUCCESS" if failed == 0 and not error else "PARTIAL" if succeeded else "FAILED"
        p = self._p
        sql = f"UPDATE ingestion_runs SET completed_at={p}, status={p}, discovered={p}, succeeded={p}, failed={p}, error={p} WHERE run_id={p}"
        with self._lock:
            self._connection.execute(sql, (_now(), status, discovered, succeeded, failed, error, run_id))
            self._connection.commit()

    def record(self, run_id: int, result: IngestionResult, metadata: dict[str, Any]) -> None:
        p = self._p
        sql = f"""INSERT INTO imagery_observations(
            run_id, adapter, external_id, source_id, state, captured_at, observed_at,
            content_sha256, content_type, byte_length, width, height, error, raw_metadata
        ) VALUES ({','.join([p] * 14)})"""
        values = (
            run_id, result.adapter, result.external_id, result.source_id, result.state,
            result.captured_at, result.observed_at, result.content_sha256, result.content_type,
            result.byte_length, result.width, result.height, result.error,
            json.dumps(metadata, sort_keys=True, separators=(",", ":")),
        )
        with self._lock:
            self._connection.execute(sql, values)
            self._connection.commit()

    def runs(self, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        p = self._p
        with self._lock:
            rows = self._connection.execute(
                f"SELECT * FROM ingestion_runs ORDER BY run_id DESC LIMIT {p}", (limit,)
            ).fetchall()
        return [self._dict(row) for row in rows]

    def observations(self, source_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 1000))
        p = self._p
        with self._lock:
            if source_id:
                rows = self._connection.execute(
                    f"SELECT * FROM imagery_observations WHERE source_id={p} ORDER BY observation_id DESC LIMIT {p}",
                    (source_id, limit),
                ).fetchall()
            else:
                rows = self._connection.execute(
                    f"SELECT * FROM imagery_observations ORDER BY observation_id DESC LIMIT {p}", (limit,)
                ).fetchall()
        values = [self._dict(row) for row in rows]
        for value in values:
            try:
                value["metadata"] = json.loads(value.pop("raw_metadata", "{}"))
            except json.JSONDecodeError:
                value["metadata"] = {}
        return values

    def latest_observation(self, adapter: str) -> dict[str, Any]:
        p = self._p
        with self._lock:
            row = self._connection.execute(
                f"SELECT * FROM imagery_observations WHERE adapter={p} ORDER BY observation_id DESC LIMIT 1",
                (adapter,),
            ).fetchone()
        value = self._dict(row)
        if value:
            try:
                value["metadata"] = json.loads(value.pop("raw_metadata", "{}"))
            except json.JSONDecodeError:
                value["metadata"] = {}
        return value

    def provider_state(self, adapter: str) -> dict[str, Any]:
        p = self._p
        with self._lock:
            row = self._connection.execute(
                f"SELECT * FROM provider_health WHERE adapter={p}", (adapter,)
            ).fetchone()
        return self._decode_provider(self._dict(row))

    @staticmethod
    def _decode_provider(value: dict[str, Any]) -> dict[str, Any]:
        if not value:
            return {}
        try:
            value["telemetry"] = json.loads(value.pop("telemetry_json", "{}"))
        except json.JSONDecodeError:
            value["telemetry"] = {}
        return value

    def provider_states(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute("SELECT * FROM provider_health ORDER BY adapter").fetchall()
        return [self._decode_provider(self._dict(row)) for row in rows]

    def upsert_provider(self, value: dict[str, Any]) -> None:
        columns = (
            "adapter", "circuit_state", "consecutive_failures", "last_status", "last_run_id",
            "last_attempt_at", "last_success_at", "next_due_at", "next_attempt_at",
            "last_error", "telemetry_json", "updated_at",
        )
        payload = {**value, "updated_at": value.get("updated_at") or _now()}
        p = self._p
        placeholders = ",".join([p] * len(columns))
        updates = ",".join(f"{column}=excluded.{column}" for column in columns[1:])
        sql = f"INSERT INTO provider_health({','.join(columns)}) VALUES ({placeholders}) ON CONFLICT(adapter) DO UPDATE SET {updates}"
        with self._lock:
            self._connection.execute(sql, tuple(payload.get(column, "") for column in columns))
            self._connection.commit()

    def record_tick(self, value: dict[str, Any]) -> None:
        p = self._p
        columns = ("started_at", "completed_at", "requested", "executed", "skipped", "successful", "failed", "details_json")
        details = value.get("details_json") or json.dumps(value.get("details", []), sort_keys=True, separators=(",", ":"))
        with self._lock:
            self._connection.execute(
                f"INSERT INTO scheduler_ticks({','.join(columns)}) VALUES ({','.join([p] * len(columns))})",
                (
                    value["started_at"], value["completed_at"], int(value["requested"]),
                    int(value["executed"]), int(value["skipped"]), int(value["successful"]),
                    int(value["failed"]), details,
                ),
            )
            self._connection.commit()

    def ticks(self, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        p = self._p
        with self._lock:
            rows = self._connection.execute(
                f"SELECT * FROM scheduler_ticks ORDER BY tick_id DESC LIMIT {p}", (limit,)
            ).fetchall()
        values = [self._dict(row) for row in rows]
        for value in values:
            try:
                value["details"] = json.loads(value.pop("details_json", "[]"))
            except json.JSONDecodeError:
                value["details"] = []
        return values
