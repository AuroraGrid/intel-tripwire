from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import app
from phase8_runtime import OperationalAggregator, operational_status, read_runtime
from release_engine import adapters


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def qualify(output: str, retries: int = 2, minimum_online: int = 4) -> dict:
    os.environ.pop("AURORA_OFFLINE", None)
    target = Path(output)
    last_error = None
    payload = None

    for attempt in range(1, max(1, retries) + 1):
        try:
            aggregate = OperationalAggregator(runtime_path=target)
            payload = aggregate.collect(force=True)
            break
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < max(1, retries):
                time.sleep(min(5, attempt * 2))

    snapshot = read_runtime(target)
    status = operational_status(snapshot, stale_after_seconds=900)
    source_rows = list(snapshot.get("sources") or [])
    online = [row for row in source_rows if row.get("status") == "online"]
    degraded = [row for row in source_rows if row.get("status") != "online"]
    expected = len(adapters(app.DEFAULT_QUERY))

    passed = bool(
        payload
        and snapshot.get("status") == "ok"
        and snapshot.get("mode") in {"live", "live_degraded"}
        and not any(row.get("status") == "offline_fallback" for row in source_rows)
        and len(online) >= max(1, int(minimum_online))
        and int(snapshot.get("event_count") or 0) > 0
        and int(snapshot.get("evidence_count") or 0) > 0
        and not status.get("stale")
    )

    result = {
        "schema_version": "1.0",
        "qualified_at": now_iso(),
        "passed": passed,
        "expected_sources": expected,
        "minimum_online": max(1, int(minimum_online)),
        "online_sources": [row.get("source") for row in online],
        "degraded_sources": [
            {
                "source": row.get("source"),
                "status": row.get("status"),
                "error": row.get("error"),
            }
            for row in degraded
        ],
        "mode": snapshot.get("mode"),
        "event_count": int(snapshot.get("event_count") or 0),
        "evidence_count": int(snapshot.get("evidence_count") or 0),
        "duplicates_suppressed": int(snapshot.get("duplicates_suppressed") or 0),
        "age_seconds": status.get("age_seconds"),
        "last_error": snapshot.get("last_error") or last_error,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="/tmp/aurora-runtime.json")
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--minimum-online", type=int, default=4)
    args = parser.parse_args()
    result = qualify(args.output, args.retries, args.minimum_online)
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
