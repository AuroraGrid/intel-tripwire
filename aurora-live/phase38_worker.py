from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from phase38_providers import TransportProviderCoordinator
from phase38_transport import TransportStore


def _target(value: str) -> str:
    if value.startswith(("postgresql://", "postgres://")) or value == ":memory:":
        return value
    Path(value).parent.mkdir(parents=True, exist_ok=True)
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Run AURORA Phase 38 transport provider ingestion")
    parser.add_argument("--provider", choices=("aviation", "maritime", "all"), default="all")
    parser.add_argument(
        "--database",
        default=os.getenv("AURORA_TRANSPORT_DB") or os.getenv("AURORA_DATABASE_URL") or "var/aurora_transport.sqlite3",
    )
    parser.add_argument("--bbox", default="-90,-180,90,180")
    parser.add_argument("--hours", type=int, default=1)
    parser.add_argument("--max-messages", type=int, default=25)
    parser.add_argument("--timeout", type=int, default=20)
    args = parser.parse_args()

    coordinator = TransportProviderCoordinator(TransportStore(_target(args.database)))
    results = []
    if args.provider in ("aviation", "all"):
        results.append(coordinator.run_aviation(bbox=args.bbox, hours=args.hours, timeout=args.timeout).value())
    if args.provider in ("maritime", "all"):
        results.append(coordinator.run_maritime(max_messages=args.max_messages, timeout=args.timeout).value())
    print(json.dumps({"results": results, "coverage": coordinator.store.coverage()}, indent=2, sort_keys=True))
    return 0 if all(row["successful"] for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
