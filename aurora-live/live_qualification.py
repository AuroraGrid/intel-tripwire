from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from phase8_runtime import OperationalAggregator, operational_status, read_runtime
from phase9_sources import phase9_adapters
import app


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def qualify(output: str, retries: int = 2, minimum_online: int = 12, minimum_capabilities: int = 8) -> dict:
    os.environ.pop("AURORA_OFFLINE", None)
    target = Path(output)
    last_error = None
    payload = None
    for attempt in range(1, max(1, retries) + 1):
        try:
            payload = OperationalAggregator(runtime_path=target).collect(force=True)
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
    capabilities = {row.get("capability") for row in online if row.get("capability")}
    expected = len(phase9_adapters(app.DEFAULT_QUERY))
    registry_totals = snapshot.get("registry_totals") or {}
    registry_passed = int(registry_totals.get("adapters") or 0) >= 25 and int(registry_totals.get("capability_classes") or 0) >= 8
    live_passed = len(online) >= max(1, int(minimum_online)) and len(capabilities) >= max(1, int(minimum_capabilities))

    passed = bool(
        payload
        and snapshot.get("status") == "ok"
        and snapshot.get("mode") in {"live", "live_degraded"}
        and not any(row.get("status") == "offline_fallback" for row in source_rows)
        and registry_passed
        and live_passed
        and int(snapshot.get("event_count") or 0) > 0
        and int(snapshot.get("evidence_count") or 0) > 0
        and not status.get("stale")
    )
    result = {
        "schema_version": "2.0",
        "qualified_at": now_iso(),
        "passed": passed,
        "registry_breadth_passed": registry_passed,
        "live_breadth_passed": live_passed,
        "expected_sources": expected,
        "minimum_online": max(1, int(minimum_online)),
        "minimum_capabilities": max(1, int(minimum_capabilities)),
        "online_sources": [row.get("source") for row in online],
        "online_capabilities": sorted(capabilities),
        "degraded_sources": [{"source": row.get("source"), "status": row.get("status"), "error": row.get("error")} for row in degraded],
        "registry_totals": registry_totals,
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
    parser.add_argument("--minimum-online", type=int, default=12)
    parser.add_argument("--minimum-capabilities", type=int, default=8)
    args = parser.parse_args()
    result = qualify(args.output, args.retries, args.minimum_online, args.minimum_capabilities)
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
