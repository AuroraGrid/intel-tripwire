from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from phase34_imagery import ImageRegistry
from phase35_sources import HttpTransport, ImageCandidate, SourceAdapter, image_dimensions, normalized_content_type, parse_http_timestamp


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class IngestionResult:
    adapter: str
    external_id: str
    source_id: str
    state: str
    captured_at: str
    observed_at: str
    content_sha256: str
    content_type: str
    byte_length: int
    width: int
    height: int
    error: str = ""

    def value(self) -> dict[str, Any]:
        return asdict(self)


class IngestionStore:
    """SQLite-backed append-only run and observation history."""

    def __init__(self, path: str | os.PathLike[str] = ":memory:") -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._lock:
            self._connection.executescript(
                """
                PRAGMA foreign_keys = ON;
                CREATE TABLE IF NOT EXISTS ingestion_runs (
                    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    adapter TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    status TEXT NOT NULL,
                    discovered INTEGER NOT NULL DEFAULT 0,
                    succeeded INTEGER NOT NULL DEFAULT 0,
                    failed INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS imagery_observations (
                    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    adapter TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    byte_length INTEGER NOT NULL,
                    width INTEGER NOT NULL,
                    height INTEGER NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    raw_metadata TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES ingestion_runs(run_id)
                );
                CREATE INDEX IF NOT EXISTS idx_observations_source_time
                    ON imagery_observations(source_id, observed_at DESC);
                CREATE INDEX IF NOT EXISTS idx_runs_adapter_time
                    ON ingestion_runs(adapter, started_at DESC);
                """
            )
            self._connection.commit()

    def start_run(self, adapter: str) -> int:
        with self._lock:
            cursor = self._connection.execute(
                "INSERT INTO ingestion_runs(adapter, started_at, status) VALUES (?, ?, 'RUNNING')",
                (adapter, _now()),
            )
            self._connection.commit()
            return int(cursor.lastrowid)

    def finish_run(self, run_id: int, *, discovered: int, succeeded: int, failed: int, error: str = "") -> None:
        status = "SUCCESS" if failed == 0 and not error else "PARTIAL" if succeeded else "FAILED"
        with self._lock:
            self._connection.execute(
                """
                UPDATE ingestion_runs
                   SET completed_at=?, status=?, discovered=?, succeeded=?, failed=?, error=?
                 WHERE run_id=?
                """,
                (_now(), status, discovered, succeeded, failed, error, run_id),
            )
            self._connection.commit()

    def record(self, run_id: int, result: IngestionResult, metadata: dict[str, Any]) -> None:
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO imagery_observations(
                    run_id, adapter, external_id, source_id, state, captured_at,
                    observed_at, content_sha256, content_type, byte_length, width,
                    height, error, raw_metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    result.adapter,
                    result.external_id,
                    result.source_id,
                    result.state,
                    result.captured_at,
                    result.observed_at,
                    result.content_sha256,
                    result.content_type,
                    result.byte_length,
                    result.width,
                    result.height,
                    result.error,
                    json.dumps(metadata, sort_keys=True, separators=(",", ":")),
                ),
            )
            self._connection.commit()

    def runs(self, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM ingestion_runs ORDER BY run_id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(row) for row in rows]

    def observations(self, source_id: str = "", limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 1000))
        with self._lock:
            if source_id:
                rows = self._connection.execute(
                    "SELECT * FROM imagery_observations WHERE source_id=? ORDER BY observation_id DESC LIMIT ?",
                    (source_id, limit),
                ).fetchall()
            else:
                rows = self._connection.execute(
                    "SELECT * FROM imagery_observations ORDER BY observation_id DESC LIMIT ?", (limit,)
                ).fetchall()
            return [dict(row) for row in rows]


class ImageryIngestionEngine:
    def __init__(self, registry: ImageRegistry, store: IngestionStore, *, transport: HttpTransport | None = None, max_image_bytes: int = 25_000_000) -> None:
        self.registry = registry
        self.store = store
        self.transport = transport or HttpTransport()
        self.max_image_bytes = max_image_bytes

    def _ingest_candidate(self, candidate: ImageCandidate) -> IngestionResult:
        registered = self.registry.register(candidate.source_payload)
        source_id = registered["source_id"]
        observed_at = _now()
        response = self.transport.get(candidate.image_url, allowed_hosts=set(candidate.allowed_hosts), max_bytes=self.max_image_bytes)
        content_type = normalized_content_type(response.headers, response.body)
        width, height = image_dimensions(response.body, content_type)
        digest = hashlib.sha256(response.body).hexdigest()
        captured_at = candidate.captured_at or parse_http_timestamp(response.headers.get("last-modified")) or observed_at
        observed = self.registry.observe(
            source_id,
            {
                "state": "FRESH",
                "observed_at": observed_at,
                "captured_at": captured_at,
                "content_sha256": digest,
                "content_type": content_type,
                "byte_length": len(response.body),
                "width": width,
                "height": height,
            },
        )
        return IngestionResult(
            adapter=candidate.adapter,
            external_id=candidate.external_id,
            source_id=source_id,
            state=observed["state"],
            captured_at=observed["last_captured_at"],
            observed_at=observed["last_observed_at"],
            content_sha256=digest,
            content_type=content_type,
            byte_length=len(response.body),
            width=width,
            height=height,
        )

    def run_adapter(self, adapter: SourceAdapter) -> dict[str, Any]:
        run_id = self.store.start_run(adapter.name)
        discovered = succeeded = failed = 0
        results: list[dict[str, Any]] = []
        fatal_error = ""
        try:
            candidates = adapter.discover(self.transport)
            discovered = len(candidates)
            for candidate in candidates:
                try:
                    result = self._ingest_candidate(candidate)
                    succeeded += 1
                    self.store.record(run_id, result, candidate.metadata)
                    results.append(result.value())
                except Exception as exc:
                    failed += 1
                    error_result = IngestionResult(
                        adapter=candidate.adapter,
                        external_id=candidate.external_id,
                        source_id="",
                        state="OFFLINE",
                        captured_at=candidate.captured_at or "",
                        observed_at=_now(),
                        content_sha256="",
                        content_type="",
                        byte_length=0,
                        width=0,
                        height=0,
                        error=str(exc),
                    )
                    self.store.record(run_id, error_result, candidate.metadata)
                    results.append(error_result.value())
        except Exception as exc:
            fatal_error = str(exc)
            failed = max(failed, 1)
        finally:
            self.store.finish_run(run_id, discovered=discovered, succeeded=succeeded, failed=failed, error=fatal_error)
        return {
            "run_id": run_id,
            "adapter": adapter.name,
            "discovered": discovered,
            "succeeded": succeeded,
            "failed": failed,
            "status": "SUCCESS" if failed == 0 and not fatal_error else "PARTIAL" if succeeded else "FAILED",
            "error": fatal_error,
            "results": results,
        }

    def run_many(self, adapters: Iterable[SourceAdapter]) -> dict[str, Any]:
        runs = [self.run_adapter(adapter) for adapter in adapters]
        return {
            "generated_at": _now(),
            "runs": runs,
            "succeeded": sum(run["succeeded"] for run in runs),
            "failed": sum(run["failed"] for run in runs),
            "all_successful": all(run["status"] == "SUCCESS" for run in runs),
        }


def default_store_path() -> str:
    return os.getenv("AURORA_INGESTION_DB", str(Path("var") / "aurora_ingestion.sqlite3"))
