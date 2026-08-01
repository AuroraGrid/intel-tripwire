from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DOMAINS = ("aviation", "maritime")
OBSERVATION_STATES = {"FRESH", "STALE", "INVALID", "UNKNOWN"}
PROVIDER_STATES = {"ONLINE", "DEGRADED", "OFFLINE", "NOT_CONFIGURED"}
WORKER_STATES = {"STARTING", "RUNNING", "DEGRADED", "STOPPED"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _is_postgres(target: str) -> bool:
    return target.startswith(("postgresql://", "postgres://"))


def _parse_time(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


@dataclass(frozen=True)
class TransportObservation:
    domain: str
    provider: str
    external_id: str
    observed_at: str
    event_time: str
    latitude: float
    longitude: float
    state: str
    payload: dict[str, Any]
    provenance: dict[str, Any]

    def value(self) -> dict[str, Any]:
        return asdict(self)


class TransportStore:
    """Shared SQLite/PostgreSQL observation, run, provider, and worker-health store."""

    def __init__(self, target: str = ":memory:") -> None:
        self.target = str(target)
        self.postgres = _is_postgres(self.target)
        self._lock = threading.RLock()
        if self.postgres:
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc:
                raise RuntimeError("psycopg is required for PostgreSQL transport storage") from exc
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
        observation_pk = "observation_id BIGSERIAL PRIMARY KEY" if self.postgres else "observation_id INTEGER PRIMARY KEY AUTOINCREMENT"
        run_pk = "run_id BIGSERIAL PRIMARY KEY" if self.postgres else "run_id INTEGER PRIMARY KEY AUTOINCREMENT"
        statements = [
            f"""CREATE TABLE IF NOT EXISTS transport_observations (
                {observation_pk}, domain TEXT NOT NULL, provider TEXT NOT NULL,
                external_id TEXT NOT NULL, observed_at TEXT NOT NULL, event_time TEXT NOT NULL,
                latitude DOUBLE PRECISION NOT NULL, longitude DOUBLE PRECISION NOT NULL,
                state TEXT NOT NULL, payload_json TEXT NOT NULL, provenance_json TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS transport_provider_health (
                provider TEXT PRIMARY KEY, domain TEXT NOT NULL, state TEXT NOT NULL,
                last_attempt_at TEXT NOT NULL, last_success_at TEXT NOT NULL,
                consecutive_failures INTEGER NOT NULL, freshness_seconds INTEGER NOT NULL,
                last_error TEXT NOT NULL, completeness_note TEXT NOT NULL,
                license_note TEXT NOT NULL, updated_at TEXT NOT NULL)""",
            f"""CREATE TABLE IF NOT EXISTS transport_provider_runs (
                {run_pk}, provider TEXT NOT NULL, domain TEXT NOT NULL,
                started_at TEXT NOT NULL, completed_at TEXT NOT NULL,
                successful INTEGER NOT NULL, observations INTEGER NOT NULL,
                duration_ms INTEGER NOT NULL, error TEXT NOT NULL,
                metadata_json TEXT NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS transport_worker_health (
                worker TEXT PRIMARY KEY, state TEXT NOT NULL, started_at TEXT NOT NULL,
                last_heartbeat_at TEXT NOT NULL, last_cycle_at TEXT NOT NULL,
                cycles INTEGER NOT NULL, failures INTEGER NOT NULL,
                last_error TEXT NOT NULL, updated_at TEXT NOT NULL)""",
            "CREATE INDEX IF NOT EXISTS idx_transport_domain_time ON transport_observations(domain, observation_id DESC)",
            "CREATE INDEX IF NOT EXISTS idx_transport_provider_time ON transport_observations(provider, observation_id DESC)",
            "CREATE INDEX IF NOT EXISTS idx_transport_runs_provider ON transport_provider_runs(provider, run_id DESC)",
            "CREATE INDEX IF NOT EXISTS idx_transport_runs_domain ON transport_provider_runs(domain, run_id DESC)",
        ]
        with self._lock:
            cursor = self._connection.cursor()
            for statement in statements:
                cursor.execute(statement)
            self._connection.commit()

    @staticmethod
    def _dict(row: Any) -> dict[str, Any]:
        return dict(row) if row is not None else {}

    @staticmethod
    def _identifier(row: Any, key: str) -> int:
        if isinstance(row, dict):
            return int(row[key])
        try:
            return int(row[key])
        except (TypeError, KeyError, IndexError):
            return int(row[0])

    def record(self, observation: TransportObservation) -> int:
        if observation.domain not in DOMAINS:
            raise ValueError("invalid transport domain")
        if observation.state not in OBSERVATION_STATES:
            raise ValueError("invalid observation state")
        if not observation.provider or not observation.external_id:
            raise ValueError("provider and external_id are required")
        values = (
            observation.domain,
            observation.provider,
            observation.external_id,
            observation.observed_at,
            observation.event_time,
            float(observation.latitude),
            float(observation.longitude),
            observation.state,
            json.dumps(observation.payload, sort_keys=True, separators=(",", ":")),
            json.dumps(observation.provenance, sort_keys=True, separators=(",", ":")),
        )
        with self._lock:
            cursor = self._connection.cursor()
            cursor.execute(
                f"SELECT observation_id FROM transport_observations WHERE domain={self._p} AND provider={self._p} AND external_id={self._p} ORDER BY observation_id DESC LIMIT 1",
                (observation.domain, observation.provider, observation.external_id),
            )
            existing = cursor.fetchone()
            if existing is not None:
                return self._identifier(existing, "observation_id")
            if self.postgres:
                cursor.execute(
                    "INSERT INTO transport_observations(domain,provider,external_id,observed_at,event_time,latitude,longitude,state,payload_json,provenance_json) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING observation_id",
                    values,
                )
                identifier = self._identifier(cursor.fetchone(), "observation_id")
            else:
                cursor.execute(
                    "INSERT INTO transport_observations(domain,provider,external_id,observed_at,event_time,latitude,longitude,state,payload_json,provenance_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    values,
                )
                identifier = int(cursor.lastrowid)
            self._connection.commit()
            return identifier

    def observations(self, domain: str = "", provider: str = "", limit: int = 250) -> list[dict[str, Any]]:
        if domain and domain not in DOMAINS:
            raise ValueError("invalid transport domain")
        limit = max(1, min(int(limit), 2000))
        clauses: list[str] = []
        values: list[Any] = []
        if domain:
            clauses.append(f"domain={self._p}")
            values.append(domain)
        if provider:
            clauses.append(f"provider={self._p}")
            values.append(provider)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(limit)
        with self._lock:
            rows = self._connection.execute(
                f"SELECT * FROM transport_observations{where} ORDER BY observation_id DESC LIMIT {self._p}",
                tuple(values),
            ).fetchall()
        output = []
        for row in rows:
            value = self._dict(row)
            for source, target in (("payload_json", "payload"), ("provenance_json", "provenance")):
                try:
                    value[target] = json.loads(value.pop(source, "{}"))
                except json.JSONDecodeError:
                    value[target] = {}
            output.append(value)
        return output

    def record_provider_run(self, value: dict[str, Any]) -> int:
        provider = str(value.get("provider") or "").strip()
        domain = str(value.get("domain") or "").strip().lower()
        if not provider:
            raise ValueError("provider is required")
        if domain not in DOMAINS:
            raise ValueError("invalid transport domain")
        values = (
            provider,
            domain,
            str(value.get("started_at") or _now()),
            str(value.get("completed_at") or _now()),
            1 if bool(value.get("successful")) else 0,
            max(0, int(value.get("observations") or 0)),
            max(0, int(value.get("duration_ms") or 0)),
            str(value.get("error") or ""),
            json.dumps(value.get("metadata") if isinstance(value.get("metadata"), dict) else {}, sort_keys=True, separators=(",", ":")),
        )
        with self._lock:
            cursor = self._connection.cursor()
            if self.postgres:
                cursor.execute(
                    "INSERT INTO transport_provider_runs(provider,domain,started_at,completed_at,successful,observations,duration_ms,error,metadata_json) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING run_id",
                    values,
                )
                identifier = self._identifier(cursor.fetchone(), "run_id")
            else:
                cursor.execute(
                    "INSERT INTO transport_provider_runs(provider,domain,started_at,completed_at,successful,observations,duration_ms,error,metadata_json) VALUES (?,?,?,?,?,?,?,?,?)",
                    values,
                )
                identifier = int(cursor.lastrowid)
            self._connection.commit()
            return identifier

    def provider_runs(self, domain: str = "", provider: str = "", limit: int = 100) -> list[dict[str, Any]]:
        if domain and domain not in DOMAINS:
            raise ValueError("invalid transport domain")
        limit = max(1, min(int(limit), 1000))
        clauses: list[str] = []
        values: list[Any] = []
        if domain:
            clauses.append(f"domain={self._p}")
            values.append(domain)
        if provider:
            clauses.append(f"provider={self._p}")
            values.append(provider)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(limit)
        with self._lock:
            rows = self._connection.execute(
                f"SELECT * FROM transport_provider_runs{where} ORDER BY run_id DESC LIMIT {self._p}",
                tuple(values),
            ).fetchall()
        output: list[dict[str, Any]] = []
        for row in rows:
            item = self._dict(row)
            item["successful"] = bool(item.get("successful"))
            try:
                item["metadata"] = json.loads(item.pop("metadata_json", "{}"))
            except json.JSONDecodeError:
                item["metadata"] = {}
            output.append(item)
        return output

    def upsert_provider(self, value: dict[str, Any]) -> None:
        domain = str(value.get("domain") or "")
        state = str(value.get("state") or "")
        if domain not in DOMAINS:
            raise ValueError("invalid transport domain")
        if state not in PROVIDER_STATES:
            raise ValueError("invalid provider state")
        columns = (
            "provider", "domain", "state", "last_attempt_at", "last_success_at",
            "consecutive_failures", "freshness_seconds", "last_error",
            "completeness_note", "license_note", "updated_at",
        )
        payload = {**value, "updated_at": value.get("updated_at") or _now()}
        p = self._p
        updates = ",".join(f"{column}=excluded.{column}" for column in columns[1:])
        sql = f"INSERT INTO transport_provider_health({','.join(columns)}) VALUES ({','.join([p] * len(columns))}) ON CONFLICT(provider) DO UPDATE SET {updates}"
        with self._lock:
            self._connection.execute(sql, tuple(payload.get(column, "") for column in columns))
            self._connection.commit()

    def providers(self, domain: str = "") -> list[dict[str, Any]]:
        if domain and domain not in DOMAINS:
            raise ValueError("invalid transport domain")
        with self._lock:
            if domain:
                rows = self._connection.execute(
                    f"SELECT * FROM transport_provider_health WHERE domain={self._p} ORDER BY provider",
                    (domain,),
                ).fetchall()
            else:
                rows = self._connection.execute("SELECT * FROM transport_provider_health ORDER BY domain, provider").fetchall()
        return [self._dict(row) for row in rows]

    def upsert_worker(self, value: dict[str, Any]) -> None:
        worker = str(value.get("worker") or "").strip()
        state = str(value.get("state") or "").strip().upper()
        if not worker:
            raise ValueError("worker is required")
        if state not in WORKER_STATES:
            raise ValueError("invalid worker state")
        columns = (
            "worker", "state", "started_at", "last_heartbeat_at", "last_cycle_at",
            "cycles", "failures", "last_error", "updated_at",
        )
        payload = {**value, "updated_at": value.get("updated_at") or _now()}
        p = self._p
        updates = ",".join(f"{column}=excluded.{column}" for column in columns[1:])
        sql = f"INSERT INTO transport_worker_health({','.join(columns)}) VALUES ({','.join([p] * len(columns))}) ON CONFLICT(worker) DO UPDATE SET {updates}"
        with self._lock:
            self._connection.execute(sql, tuple(payload.get(column, "") for column in columns))
            self._connection.commit()

    def workers(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute("SELECT * FROM transport_worker_health ORDER BY worker").fetchall()
        return [self._dict(row) for row in rows]

    def health(self, max_age_seconds: int | None = None, run_limit: int = 20) -> dict[str, Any]:
        maximum_age = max(30, int(max_age_seconds or os.getenv("AURORA_TRANSPORT_STALE_SECONDS", "900")))
        now = datetime.now(timezone.utc)
        providers = self.providers()
        observations = self.observations(limit=2000)
        recent_runs = self.provider_runs(limit=run_limit)
        provider_health: list[dict[str, Any]] = []
        for provider in providers:
            last_success = _parse_time(provider.get("last_success_at", ""))
            age = int((now - last_success).total_seconds()) if last_success else None
            provider_observations = [row for row in observations if row["provider"] == provider["provider"]]
            provider_runs = [row for row in recent_runs if row["provider"] == provider["provider"]]
            latest_run = provider_runs[0] if provider_runs else None
            freshness_seconds = max(0, int(provider.get("freshness_seconds") or 0))
            fresh = bool(
                provider["state"] == "ONLINE"
                and age is not None
                and age <= maximum_age
                and freshness_seconds <= maximum_age
            )
            operational = bool(fresh and provider_observations and latest_run and latest_run["successful"] and latest_run["observations"] > 0)
            provider_health.append(
                {
                    **provider,
                    "seconds_since_success": age,
                    "fresh": fresh,
                    "operational": operational,
                    "observations": len(provider_observations),
                    "latest_run": latest_run,
                }
            )
        domains = []
        for domain in DOMAINS:
            rows = [row for row in provider_health if row["domain"] == domain]
            domains.append(
                {
                    "domain": domain,
                    "providers": len(rows),
                    "operational_providers": sum(1 for row in rows if row["operational"]),
                    "operational": any(row["operational"] for row in rows),
                }
            )
        return {
            "max_age_seconds": maximum_age,
            "providers": provider_health,
            "domains": domains,
            "operational_domains": sum(1 for row in domains if row["operational"]),
            "fully_operational": all(row["operational"] for row in domains),
            "workers": self.workers(),
            "generated_at": _now(),
        }

    def coverage(self) -> dict[str, Any]:
        health = self.health(run_limit=20)
        domains = []
        for row in health["domains"]:
            domain = row["domain"]
            domain_providers = [provider for provider in health["providers"] if provider["domain"] == domain]
            domains.append(
                {
                    "domain": domain,
                    "providers_registered": len(domain_providers),
                    "providers_online": sum(1 for provider in domain_providers if provider["state"] == "ONLINE"),
                    "observations": sum(int(provider["observations"]) for provider in domain_providers),
                    "qualified": bool(row["operational"]),
                }
            )
        return {
            "requirement": "each transport domain requires a licensed provider with a recent successful run and fresh, durably persisted observations",
            "domains": domains,
            "qualified_domains": sum(row["qualified"] for row in domains),
            "fully_qualified": all(row["qualified"] for row in domains),
            "generated_at": _now(),
        }

    def close(self) -> None:
        """Close the underlying database connection to release file handles (Windows-safe)."""
        try:
            with self._lock:
                conn = getattr(self, "_connection", None)
                if conn is None:
                    return
                try:
                    conn.close()
                except Exception:
                    pass
                finally:
                    self._connection = None
        except Exception:
            pass

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


class TransportRegistry:
    """Provider registration never implies live operational qualification."""

    def __init__(self, store: TransportStore) -> None:
        self.store = store

    def register_provider(self, payload: dict[str, Any]) -> dict[str, Any]:
        provider = str(payload.get("provider") or "").strip()
        domain = str(payload.get("domain") or "").strip().lower()
        if not provider:
            raise ValueError("provider is required")
        if domain not in DOMAINS:
            raise ValueError("invalid transport domain")
        value = {
            "provider": provider,
            "domain": domain,
            "state": "NOT_CONFIGURED",
            "last_attempt_at": "",
            "last_success_at": "",
            "consecutive_failures": 0,
            "freshness_seconds": 0,
            "last_error": "",
            "completeness_note": str(payload.get("completeness_note") or "Coverage is provider-dependent and not assumed global."),
            "license_note": str(payload.get("license_note") or "License and redistribution terms must be verified before public use."),
        }
        self.store.upsert_provider(value)
        return value

    def observe_provider(self, provider: str, payload: dict[str, Any]) -> dict[str, Any]:
        existing = next((row for row in self.store.providers() if row["provider"] == provider), None)
        if existing is None:
            raise KeyError("transport provider not found")
        successful = bool(payload.get("successful"))
        now = str(payload.get("observed_at") or _now())
        failures = 0 if successful else int(existing.get("consecutive_failures", 0)) + 1
        state = "ONLINE" if successful else "OFFLINE" if failures >= 3 else "DEGRADED"
        value = {
            **existing,
            "state": state,
            "last_attempt_at": now,
            "last_success_at": now if successful else existing.get("last_success_at", ""),
            "consecutive_failures": failures,
            "freshness_seconds": max(0, int(payload.get("freshness_seconds") or 0)),
            "last_error": "" if successful else str(payload.get("error") or "provider check failed"),
        }
        self.store.upsert_provider(value)
        return value
