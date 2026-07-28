from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from phase34_imagery import ImageRegistry
from phase36_operations import OperationalCoordinator
from phase36_sources import operational_adapter_names
from phase36_store import OperationalStore


def _target(value: str) -> str:
    if value.startswith(("postgresql://", "postgres://")) or value == ":memory:":
        return value
    Path(value).parent.mkdir(parents=True, exist_ok=True)
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Run recurring AURORA Phase 36 official-source ingestion")
    parser.add_argument("--adapter", action="append", choices=operational_adapter_names(), help="Adapter to run; repeatable")
    parser.add_argument(
        "--database",
        default=os.getenv("AURORA_OPERATIONAL_DB") or os.getenv("AURORA_DATABASE_URL") or "var/aurora_operations.sqlite3",
        help="SQLite path or PostgreSQL DSN",
    )
    parser.add_argument("--force", action="store_true", help="Run even when not due or circuit-open cooldown is active")
    parser.add_argument("--loop", action="store_true", help="Run continuously")
    parser.add_argument("--sleep-seconds", type=int, default=60, help="Loop sleep interval")
    args = parser.parse_args()

    store = OperationalStore(_target(args.database))
    coordinator = OperationalCoordinator(ImageRegistry(), store)
    names = args.adapter or list(operational_adapter_names())
    while True:
        result = coordinator.run_due(names, force=args.force)
        print(json.dumps(result, indent=2, sort_keys=True), flush=True)
        if not args.loop:
            return 0 if result["failed"] == 0 else 1
        time.sleep(max(15, args.sleep_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
