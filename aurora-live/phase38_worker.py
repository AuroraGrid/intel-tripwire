from __future__ import annotations

import argparse
import json
import os
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from phase38_providers import ProviderRun, TransportProviderCoordinator
from phase38_transport import TransportStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _target(value: str) -> str:
    if value.startswith(("postgresql://", "postgres://")) or value == ":memory:":
        return value
    Path(value).parent.mkdir(parents=True, exist_ok=True)
    return value


class TransportOperationalWorker:
    """Recurring aviation and maritime ingestion with durable worker telemetry."""

    def __init__(
        self,
        store: TransportStore,
        *,
        coordinator: TransportProviderCoordinator | None = None,
        provider: str = "all",
        aviation_interval: int = 300,
        maritime_interval: int = 60,
        heartbeat_interval: int = 30,
        bbox: str = "-90,-180,90,180",
        hours: int = 1,
        max_messages: int = 25,
        timeout: int = 20,
        worker_name: str = "phase38-transport",
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if provider not in {"aviation", "maritime", "all"}:
            raise ValueError("invalid transport provider selection")
        self.store = store
        self.coordinator = coordinator or TransportProviderCoordinator(store)
        self.provider = provider
        self.aviation_interval = max(60, int(aviation_interval))
        self.maritime_interval = max(15, int(maritime_interval))
        self.heartbeat_interval = max(5, int(heartbeat_interval))
        self.bbox = bbox
        self.hours = max(1, min(24, int(hours)))
        self.max_messages = max(1, min(500, int(max_messages)))
        self.timeout = max(5, min(120, int(timeout)))
        self.worker_name = worker_name
        self.clock = clock
        self.sleeper = sleeper
        self.started_at = _now()
        self.cycles = 0
        self.failures = 0
        self.last_cycle_at = ""
        self._stop = False
        self._next_due = {"aviation": 0.0, "maritime": 0.0}

    def stop(self, *_args: Any) -> None:
        self._stop = True

    def _selected(self) -> tuple[str, ...]:
        if self.provider == "all":
            return ("aviation", "maritime")
        return (self.provider,)

    def _heartbeat(self, state: str, error: str = "") -> None:
        self.store.upsert_worker(
            {
                "worker": self.worker_name,
                "state": state,
                "started_at": self.started_at,
                "last_heartbeat_at": _now(),
                "last_cycle_at": self.last_cycle_at,
                "cycles": self.cycles,
                "failures": self.failures,
                "last_error": error,
            }
        )

    def run_cycle(self, *, force: bool = False) -> dict[str, Any]:
        now_clock = self.clock()
        results: list[ProviderRun] = []
        for name in self._selected():
            if not force and now_clock < self._next_due[name]:
                continue
            if name == "aviation":
                result = self.coordinator.run_aviation(
                    bbox=self.bbox,
                    hours=self.hours,
                    timeout=self.timeout,
                )
                self._next_due[name] = now_clock + self.aviation_interval
            else:
                result = self.coordinator.run_maritime(
                    max_messages=self.max_messages,
                    timeout=self.timeout,
                )
                self._next_due[name] = now_clock + self.maritime_interval
            results.append(result)

        if results:
            self.cycles += 1
            failed = [row for row in results if not row.successful]
            self.failures += len(failed)
            self.last_cycle_at = _now()
            state = "DEGRADED" if failed else "RUNNING"
            error = "; ".join(row.error for row in failed if row.error)
        else:
            state = "RUNNING"
            error = ""
        self._heartbeat(state, error)
        return {
            "worker": self.worker_name,
            "state": state,
            "cycles": self.cycles,
            "failures": self.failures,
            "results": [row.value() for row in results],
            "coverage": self.store.coverage(),
            "health": self.store.health(),
        }

    def _sleep_seconds(self) -> float:
        selected = self._selected()
        now_clock = self.clock()
        next_due = min(self._next_due[name] for name in selected)
        return max(1.0, min(float(self.heartbeat_interval), max(0.0, next_due - now_clock)))

    def run(self, *, once: bool = False, max_cycles: int = 0) -> int:
        self._heartbeat("STARTING")
        first = True
        exit_code = 0
        try:
            while not self._stop:
                result = self.run_cycle(force=first)
                first = False
                print(json.dumps(result, sort_keys=True), flush=True)
                if any(not row.get("successful") for row in result["results"]):
                    exit_code = 1
                if once or (max_cycles and self.cycles >= max_cycles):
                    break
                self.sleeper(self._sleep_seconds())
        finally:
            self._heartbeat("STOPPED")
        return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description="Run AURORA Phase 38 transport provider ingestion")
    parser.add_argument(
        "--provider",
        choices=("aviation", "maritime", "all"),
        default=os.getenv("AURORA_TRANSPORT_PROVIDERS", "all"),
    )
    parser.add_argument(
        "--database",
        default=(
            os.getenv("AURORA_TRANSPORT_DB")
            or os.getenv("AURORA_DATABASE_URL")
            or os.getenv("DATABASE_URL")
            or "var/aurora_transport.sqlite3"
        ),
    )
    parser.add_argument("--bbox", default=os.getenv("AURORA_AVIATION_BBOX", "-90,-180,90,180"))
    parser.add_argument("--hours", type=int, default=int(os.getenv("AURORA_AVIATION_HOURS", "1")))
    parser.add_argument("--max-messages", type=int, default=int(os.getenv("AURORA_AIS_MAX_MESSAGES", "25")))
    parser.add_argument("--timeout", type=int, default=int(os.getenv("AURORA_TRANSPORT_TIMEOUT_SECONDS", "20")))
    parser.add_argument("--aviation-interval", type=int, default=int(os.getenv("AURORA_AVIATION_INTERVAL_SECONDS", "300")))
    parser.add_argument("--maritime-interval", type=int, default=int(os.getenv("AURORA_MARITIME_INTERVAL_SECONDS", "60")))
    parser.add_argument("--heartbeat-interval", type=int, default=int(os.getenv("AURORA_TRANSPORT_HEARTBEAT_SECONDS", "30")))
    parser.add_argument("--worker-name", default=os.getenv("AURORA_TRANSPORT_WORKER_NAME", "phase38-transport"))
    parser.add_argument("--loop", action="store_true", help="Run continuously")
    parser.add_argument("--cycles", type=int, default=0, help="Stop after this many cycles; zero means unlimited")
    args = parser.parse_args()

    store = TransportStore(_target(args.database))
    worker = TransportOperationalWorker(
        store,
        provider=args.provider,
        aviation_interval=args.aviation_interval,
        maritime_interval=args.maritime_interval,
        heartbeat_interval=args.heartbeat_interval,
        bbox=args.bbox,
        hours=args.hours,
        max_messages=args.max_messages,
        timeout=args.timeout,
        worker_name=args.worker_name,
    )
    signal.signal(signal.SIGTERM, worker.stop)
    signal.signal(signal.SIGINT, worker.stop)
    return worker.run(once=not args.loop, max_cycles=max(0, args.cycles))


if __name__ == "__main__":
    raise SystemExit(main())
