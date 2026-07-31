from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from phase40_markets import MarketStore
from phase40_repairs import ProductionMarketCoordinator

KEYLESS_PROVIDERS = (
    "world-bank-pink-sheet",
    "ecb-reference-rates",
    "coinbase-exchange-ticker",
    "world-bank-indicators-v2",
    "kalshi-public-markets",
)


def qualify(database: str, *, retries: int = 2, timeout: int = 45) -> dict:
    store = MarketStore(database)
    coordinator = ProductionMarketCoordinator(store)
    results = []
    for provider in KEYLESS_PROVIDERS:
        result = None
        attempts = []
        for attempt in range(1, max(1, retries) + 1):
            result = coordinator.run(provider, timeout=timeout)
            attempts.append(result.value())
            if result.successful:
                break
            if attempt < max(1, retries):
                time.sleep(min(5, attempt * 2))
        results.append({"provider": provider, "passed": bool(result and result.successful), "attempts": attempts})
    passed = all(row["passed"] for row in results)
    return {
        "schema_version": "1.1",
        "passed": passed,
        "required_providers": list(KEYLESS_PROVIDERS),
        "passed_providers": [row["provider"] for row in results if row["passed"]],
        "failed_providers": [row["provider"] for row in results if not row["passed"]],
        "results": results,
        "coverage": store.coverage(),
        "health": store.health(),
        "credentialed_domains_remain_separate": ["equities", "energy"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default="/tmp/aurora-phase40-live.sqlite3")
    parser.add_argument("--output", default="/tmp/aurora-phase40-live.json")
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=45)
    args = parser.parse_args()
    result = qualify(args.database, retries=args.retries, timeout=args.timeout)
    Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "passed_providers": result["passed_providers"], "failed_providers": result["failed_providers"]}, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
