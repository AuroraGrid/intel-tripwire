from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from phase39_infrastructure import InfrastructureCoordinator, InfrastructureStore


def _target(value: str) -> str:
    if value.startswith(("postgresql://", "postgres://")) or value == ":memory:":
        return value
    Path(value).parent.mkdir(parents=True, exist_ok=True)
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Run AURORA Phase 39 infrastructure-risk ingestion")
    parser.add_argument(
        "--database",
        default=(
            os.getenv("AURORA_INFRASTRUCTURE_DB")
            or os.getenv("AURORA_OPERATIONAL_DB")
            or os.getenv("AURORA_DATABASE_URL")
            or os.getenv("DATABASE_URL")
            or "var/aurora_infrastructure.sqlite3"
        ),
    )
    parser.add_argument("--provider", default="all")
    parser.add_argument("--timeout", type=int, default=int(os.getenv("AURORA_INFRASTRUCTURE_TIMEOUT_SECONDS", "30")))
    parser.add_argument("--require-all", action="store_true", help="Fail unless all eight layers qualify")
    args = parser.parse_args()

    store = InfrastructureStore(_target(args.database))
    coordinator = InfrastructureCoordinator(store)
    if args.provider == "all":
        results = coordinator.run_all(timeout=max(5, min(args.timeout, 120)))
    else:
        results = [coordinator.run(args.provider, timeout=max(5, min(args.timeout, 120)))]

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
    if args.require_all and not payload["coverage"]["fully_qualified"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
