from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from phase34_imagery import ImageRegistry
from phase35_ingestion import ImageryIngestionEngine, IngestionStore, default_store_path
from phase35_sources import adapter_names, build_adapter


def main() -> int:
    parser = argparse.ArgumentParser(description="Run AURORA Phase 35 official-source imagery ingestion")
    parser.add_argument("--adapter", action="append", choices=adapter_names(), help="Adapter to run; repeatable")
    parser.add_argument("--database", default=default_store_path(), help="SQLite history database")
    args = parser.parse_args()

    if args.database != ":memory:":
        Path(args.database).parent.mkdir(parents=True, exist_ok=True)
    registry = ImageRegistry()
    store = IngestionStore(args.database)
    engine = ImageryIngestionEngine(registry, store)
    names = args.adapter or list(adapter_names())
    result = engine.run_many(build_adapter(name) for name in names)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["all_successful"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
