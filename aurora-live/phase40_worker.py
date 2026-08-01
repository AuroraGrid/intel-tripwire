from __future__ import annotations

import argparse
import json
import os
import signal
import time
from pathlib import Path

from phase40_markets import MarketCoordinator, MarketStore


def _target(value: str) -> str:
    if value.startswith(("postgresql://", "postgres://")) or value == ":memory:":
        return value
    Path(value).parent.mkdir(parents=True, exist_ok=True)
    return value


def _run_once(store, coordinator, provider: str, timeout: int, require_all: bool) -> int:
    if provider == "all":
        results = coordinator.run_all(timeout=max(5, min(timeout, 120)))
    else:
        results = [coordinator.run(provider, timeout=max(5, min(timeout, 120)))]

    payload = {
        "results": [row.value() for row in results],
        "coverage": store.coverage(),
        "health": store.health(),
        "configuration": coordinator.configuration(),
    }
    print(json.dumps(payload, sort_keys=True), flush=True)

    configured_failures = [row for row in results if row.configured and not row.successful]
    if configured_failures:
        return 1
    if require_all and not payload["coverage"]["fully_qualified"]:
        return 2
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run AURORA Phase 40 markets ingestion")
    parser.add_argument(
        "--database",
        default=(
            os.getenv("AURORA_MARKETS_DB")
            or os.getenv("AURORA_OPERATIONAL_DB")
            or os.getenv("AURORA_DATABASE_URL")
            or os.getenv("DATABASE_URL")
            or "var/aurora_markets.sqlite3"
        ),
    )
    parser.add_argument("--provider", default="all")
    parser.add_argument("--timeout", type=int, default=int(os.getenv("AURORA_MARKETS_TIMEOUT_SECONDS", "30")))
    parser.add_argument("--require-all", action="store_true", help="Fail unless all market layers qualify")
    parser.add_argument("--loop", action="store_true", help="Run continuously")
    parser.add_argument(
        "--interval",
        type=int,
        default=int(os.getenv("AURORA_MARKETS_INTERVAL_SECONDS", "300")),
        help="Seconds between loop cycles",
    )
    args = parser.parse_args()

    store = MarketStore(_target(args.database))
    coordinator = MarketCoordinator(store)
    stopping = {"value": False}

    def _stop(*_args):
        stopping["value"] = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    if not args.loop:
        return _run_once(store, coordinator, args.provider, args.timeout, args.require_all)

    exit_code = 0
    interval = max(30, int(args.interval))
    while not stopping["value"]:
        code = _run_once(store, coordinator, args.provider, args.timeout, args.require_all)
        if code != 0:
            exit_code = code
        for _ in range(interval):
            if stopping["value"]:
                break
            time.sleep(1)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
