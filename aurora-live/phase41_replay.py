from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

DOMAINS = (
    "events",
    "transport",
    "infrastructure",
    "markets",
    "webcams",
    "media",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _is_postgres(target: str) -> bool:
    return target.startswith(("postgresql://", "postgres://"))


@dataclass(frozen=True)
class ReplayRecord:
    domain: str
    record_id: str
    event_time: str
    title: str
    summary: str
    source_url: str
    payload: dict[str, Any]
    provenance: dict[str, Any]

    def value(self) -> dict[str, Any]:
        return asdict(self)


class ReplayStore:
    """Append-only unified replay ledger across AURORA LIVE domains."""

    def __init__(self, target: str = ":memory:") -> None:
        self.target = str(target)
        self.postgres = _is_postgres(self.target)
        self._lock = threading.RLock()
        if self.postgres:
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc:
                raise RuntimeError("psycopg is required for PostgreSQL replay storage") from exc
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
        pk = "replay_id BIGSERIAL PRIMARY KEY" if self.postgres else "replay_id INTEGER PRIMARY KEY AUTOINCREMENT"
        with self._lock:
            self._connection.execute(
                f"""CREATE TABLE IF NOT EXISTS replay_records (
                    {pk}, domain TEXT NOT NULL, record_id TEXT NOT NULL, event_time TEXT NOT NULL,
                    title TEXT NOT NULL, summary TEXT NOT NULL, source_url TEXT NOT NULL,
                    payload_json TEXT NOT NULL, provenance_json TEXT NOT NULL, ingested_at TEXT NOT NULL
                )"""
            )
            self._connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_replay_identity ON replay_records(domain,record_id)"
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_replay_time ON replay_records(event_time DESC, replay_id DESC)"
            )
            self._connection.commit()

    @staticmethod
    def _dict(row: Any) -> dict[str, Any]:
        return dict(row) if row is not None else {}

    def ingest(self, record: ReplayRecord) -> str:
        if record.domain not in DOMAINS:
            raise ValueError("invalid replay domain")
        if not record.record_id:
            raise ValueError("record_id is required")
        values = (
            record.domain,
            record.record_id,
            record.event_time or _now(),
            record.title[:500],
            record.summary[:2000],
            record.source_url,
            json.dumps(record.payload, sort_keys=True, separators=(",", ":")),
            json.dumps(record.provenance, sort_keys=True, separators=(",", ":")),
            _now(),
        )
        with self._lock:
            cursor = self._connection.cursor()
            cursor.execute(
                f"SELECT record_id FROM replay_records WHERE domain={self._p} AND record_id={self._p}",
                (record.domain, record.record_id),
            )
            if cursor.fetchone() is not None:
                return record.record_id
            cursor.execute(
                f"INSERT INTO replay_records(domain,record_id,event_time,title,summary,source_url,payload_json,provenance_json,ingested_at) VALUES ({','.join([self._p]*9)})",
                values,
            )
            self._connection.commit()
        return record.record_id

    def query(
        self,
        *,
        domains: Iterable[str] | None = None,
        start: str = "",
        end: str = "",
        limit: int = 250,
    ) -> list[dict[str, Any]]:
        selected = [str(item).strip() for item in (domains or []) if str(item).strip()]
        for domain in selected:
            if domain not in DOMAINS:
                raise ValueError("invalid replay domain")
        clauses: list[str] = []
        values: list[Any] = []
        if selected:
            placeholders = ",".join([self._p] * len(selected))
            clauses.append(f"domain IN ({placeholders})")
            values.extend(selected)
        if start:
            clauses.append(f"event_time>={self._p}")
            values.append(start)
        if end:
            clauses.append(f"event_time<={self._p}")
            values.append(end)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(max(1, min(int(limit), 2000)))
        with self._lock:
            rows = self._connection.execute(
                f"SELECT * FROM replay_records{where} ORDER BY event_time DESC, replay_id DESC LIMIT {self._p}",
                tuple(values),
            ).fetchall()
        output = []
        for row in rows:
            item = self._dict(row)
            for source, target in (("payload_json", "payload"), ("provenance_json", "provenance")):
                try:
                    item[target] = json.loads(item.pop(source, "{}"))
                except json.JSONDecodeError:
                    item[target] = {}
            output.append(item)
        return output

    def merge_external(self, records: Iterable[dict[str, Any]], *, domain: str) -> int:
        count = 0
        for item in records:
            record_id = str(item.get("record_id") or item.get("external_id") or item.get("observation_id") or "").strip()
            if not record_id:
                digest = hashlib.sha256(json.dumps(item, sort_keys=True, default=str).encode("utf-8")).hexdigest()
                record_id = digest[:24]
            self.ingest(
                ReplayRecord(
                    domain=domain,
                    record_id=f"{domain}:{record_id}",
                    event_time=str(item.get("event_time") or item.get("observed_at") or item.get("updated_at") or _now()),
                    title=str(item.get("title") or item.get("symbol") or item.get("name") or record_id),
                    summary=str(item.get("summary") or item.get("description") or "")[:2000],
                    source_url=str(item.get("source_url") or ""),
                    payload=item if isinstance(item, dict) else {},
                    provenance=item.get("provenance") if isinstance(item.get("provenance"), dict) else {"domain": domain},
                )
            )
            count += 1
        return count

    def coverage(self) -> dict[str, Any]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT domain, COUNT(*) AS total FROM replay_records GROUP BY domain"
            ).fetchall()
        counts = {domain: 0 for domain in DOMAINS}
        for row in rows:
            item = self._dict(row)
            counts[str(item.get("domain"))] = int(item.get("total") or 0)
        return {
            "domains": DOMAINS,
            "counts": counts,
            "total": sum(counts.values()),
            "generated_at": _now(),
        }
