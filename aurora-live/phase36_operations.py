from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable

from phase35_ingestion import ImageryIngestionEngine
from phase35_sources import HttpTransport
from phase36_sources import BASELINE_REGION_ADAPTERS, build_operational_adapter, operational_adapter_names, policy_for
from phase36_store import OperationalStore
from phase8_runtime import operational_status, read_runtime


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


class TelemetryHttpTransport(HttpTransport):
    """Phase 35 bounded transport with per-request operational telemetry."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._telemetry: dict[str, dict[str, Any]] = {}

    def clear(self) -> None:
        self._telemetry = {}

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return dict(self._telemetry)

    def get(self, url: str, *, allowed_hosts: set[str], max_bytes: int):
        started = time.monotonic()
        try:
            response = super().get(url, allowed_hosts=allowed_hosts, max_bytes=max_bytes)
        except Exception as exc:
            self._telemetry[url] = {
                "status": 0,
                "duration_ms": round((time.monotonic() - started) * 1000),
                "error": f"{type(exc).__name__}: {exc}"[:500],
            }
            raise
        headers = response.headers
        self._telemetry[url] = {
            "status": response.status,
            "duration_ms": round((time.monotonic() - started) * 1000),
            "byte_length": len(response.body),
            "content_type": headers.get("content-type", ""),
            "etag": headers.get("etag", ""),
            "last_modified": headers.get("last-modified", ""),
            "rate_limit": headers.get("x-ratelimit-limit", ""),
            "rate_remaining": headers.get("x-ratelimit-remaining", ""),
            "retry_after": headers.get("retry-after", ""),
        }
        return response


class OperationalCoordinator:
    def __init__(
        self,
        registry,
        store: OperationalStore,
        *,
        transport: HttpTransport | None = None,
        clock: Callable[[], datetime] = _now_dt,
    ) -> None:
        self.registry = registry
        self.store = store
        self.transport = transport or TelemetryHttpTransport()
        self.clock = clock
        self.engine = ImageryIngestionEngine(registry, store, transport=self.transport)

    def _default_state(self, adapter: str) -> dict[str, Any]:
        return {
            "adapter": adapter,
            "circuit_state": "CLOSED",
            "consecutive_failures": 0,
            "last_status": "NEVER_RUN",
            "last_run_id": None,
            "last_attempt_at": "",
            "last_success_at": "",
            "next_due_at": "",
            "next_attempt_at": "",
            "last_error": "",
            "telemetry": {},
        }

    def _telemetry(self) -> dict[str, Any]:
        snapshot = getattr(self.transport, "snapshot", None)
        return snapshot() if callable(snapshot) else {}

    def _clear_telemetry(self) -> None:
        clear = getattr(self.transport, "clear", None)
        if callable(clear):
            clear()

    def run_adapter(self, name: str, *, force: bool = False) -> dict[str, Any]:
        if name not in operational_adapter_names():
            raise ValueError(f"unknown operational adapter: {name}")
        now = self.clock().astimezone(timezone.utc)
        policy = policy_for(name)
        state = {**self._default_state(name), **self.store.provider_state(name)}
        circuit = str(state.get("circuit_state") or "CLOSED")
        next_attempt = _parse(state.get("next_attempt_at"))
        next_due = _parse(state.get("next_due_at"))
        if not force and circuit == "OPEN" and (next_attempt is None or now < next_attempt):
            return {"adapter": name, "executed": False, "status": "CIRCUIT_OPEN", "next_attempt_at": state.get("next_attempt_at", "")}
        if not force and circuit != "OPEN" and next_due and now < next_due:
            return {"adapter": name, "executed": False, "status": "NOT_DUE", "next_due_at": state.get("next_due_at", "")}

        if circuit == "OPEN":
            circuit = "HALF_OPEN"
        self._clear_telemetry()
        result = self.engine.run_adapter(build_operational_adapter(name))
        success = result["status"] == "SUCCESS" and result["succeeded"] > 0
        failures = 0 if success else int(state.get("consecutive_failures") or 0) + 1
        if success:
            circuit = "CLOSED"
            next_attempt_at = ""
        elif failures >= policy.failure_threshold:
            circuit = "OPEN"
            next_attempt_at = _iso(now + timedelta(seconds=policy.cooldown_seconds))
        else:
            circuit = "CLOSED"
            next_attempt_at = ""
        error = str(result.get("error") or "")
        if not error and result.get("results"):
            error = "; ".join(str(row.get("error") or "") for row in result["results"] if row.get("error"))[:1000]
        provider = {
            "adapter": name,
            "circuit_state": circuit,
            "consecutive_failures": failures,
            "last_status": "SUCCESS" if success else "FAILED",
            "last_run_id": result.get("run_id"),
            "last_attempt_at": _iso(now),
            "last_success_at": _iso(now) if success else state.get("last_success_at", ""),
            "next_due_at": _iso(now + timedelta(seconds=policy.interval_seconds)),
            "next_attempt_at": next_attempt_at,
            "last_error": error,
            "telemetry_json": json.dumps(self._telemetry(), sort_keys=True, separators=(",", ":")),
        }
        self.store.upsert_provider(provider)
        return {**result, "executed": True, "circuit_state": circuit, "next_due_at": provider["next_due_at"]}

    def run_due(self, names: Iterable[str] | None = None, *, force: bool = False) -> dict[str, Any]:
        started = self.clock().astimezone(timezone.utc)
        requested = list(names or operational_adapter_names())
        details = [self.run_adapter(name, force=force) for name in requested]
        executed = sum(bool(row.get("executed")) for row in details)
        skipped = len(details) - executed
        successful = sum(row.get("status") == "SUCCESS" for row in details)
        failed = sum(bool(row.get("executed")) and row.get("status") not in {"SUCCESS"} for row in details)
        completed = self.clock().astimezone(timezone.utc)
        payload = {
            "started_at": _iso(started),
            "completed_at": _iso(completed),
            "requested": len(details),
            "executed": executed,
            "skipped": skipped,
            "successful": successful,
            "failed": failed,
            "details": details,
        }
        self.store.record_tick(payload)
        return payload


class UnifiedSourceHealth:
    def __init__(self, webcams, imagery, store: OperationalStore, runtime_reader: Callable[[], dict[str, Any]] = read_runtime) -> None:
        self.webcams = webcams
        self.imagery = imagery
        self.store = store
        self.runtime_reader = runtime_reader

    @staticmethod
    def _score(state: str) -> int:
        return {
            "ONLINE": 100,
            "LIVE": 100,
            "DEGRADED": 60,
            "STALE": 35,
            "FALLBACK": 30,
            "OFFLINE": 0,
            "ERROR": 0,
            "NOT_CONFIGURED": 0,
        }.get(str(state or "").upper(), 0)

    def snapshot(self) -> dict[str, Any]:
        webcam_health = self.webcams.source_health()
        imagery_health = self.imagery.source_health()
        runtime = operational_status(self.runtime_reader())
        event_state = str(runtime.get("state") or "error").upper()
        providers = self.store.provider_states()
        provider_online = sum(row.get("last_status") == "SUCCESS" and row.get("circuit_state") == "CLOSED" for row in providers)
        provider_state = "ONLINE" if providers and provider_online == len(providers) else "DEGRADED" if provider_online else "OFFLINE" if providers else "NOT_CONFIGURED"
        feeds = [
            {"feed": "events", "state": event_state, "score": self._score(event_state), "details": runtime},
            {"feed": "webcams", "state": webcam_health["state"], "score": self._score(webcam_health["state"]), "details": webcam_health},
            {"feed": "imagery", "state": imagery_health["state"], "score": self._score(imagery_health["state"]), "details": imagery_health},
            {
                "feed": "ingestion-providers",
                "state": provider_state,
                "score": self._score(provider_state),
                "details": {"providers": providers, "online": provider_online, "total": len(providers)},
            },
        ]
        configured = [row["score"] for row in feeds if row["state"] != "NOT_CONFIGURED"]
        score = round(sum(configured) / len(configured)) if configured else 0
        state = "ONLINE" if score >= 90 else "DEGRADED" if score >= 40 else "OFFLINE"
        return {"state": state, "score": score, "feeds": feeds, "generated_at": _iso(_now_dt())}


def regional_baseline(registry, store: OperationalStore) -> dict[str, Any]:
    del registry
    providers = {row["adapter"]: row for row in store.provider_states()}
    regions = []
    for region, adapter in BASELINE_REGION_ADAPTERS.items():
        provider = providers.get(adapter, {})
        observation = store.latest_observation(adapter)
        qualified_observation = observation.get("state") in {"FRESH", "STALE"} and bool(observation.get("content_sha256"))
        verified = provider.get("last_status") == "SUCCESS" and qualified_observation
        regions.append(
            {
                "region": region,
                "expected_adapter": adapter,
                "provider_status": provider.get("last_status", "NEVER_RUN"),
                "circuit_state": provider.get("circuit_state", "CLOSED"),
                "observation_state": observation.get("state", "UNKNOWN"),
                "last_observed_at": observation.get("observed_at", ""),
                "verified": verified,
            }
        )
    return {
        "requirement": "one successful, validated, hashed and durably persisted official imagery observation per region",
        "regions": regions,
        "qualified_regions": sum(row["verified"] for row in regions),
        "fully_qualified": all(row["verified"] for row in regions),
        "generated_at": _iso(_now_dt()),
    }
