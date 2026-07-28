from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from phase38_transport import TransportStore


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def main() -> int:
    target = os.getenv("AURORA_TRANSPORT_DB") or os.getenv("AURORA_DATABASE_URL") or "var/aurora_transport.sqlite3"
    worker_name = os.getenv("AURORA_TRANSPORT_WORKER_NAME", "phase38-transport")
    max_age = max(30, int(os.getenv("AURORA_TRANSPORT_HEARTBEAT_STALE_SECONDS", "120")))
    store = TransportStore(target)
    worker = next((row for row in store.workers() if row["worker"] == worker_name), None)
    if worker is None:
        print(json.dumps({"worker": worker_name, "healthy": False, "reason": "heartbeat missing"}))
        return 1
    try:
        age = max(0, int((datetime.now(timezone.utc) - _parse(worker["last_heartbeat_at"])).total_seconds()))
    except (TypeError, ValueError):
        age = max_age + 1
    healthy = worker["state"] in {"RUNNING", "DEGRADED"} and age <= max_age
    print(
        json.dumps(
            {
                "worker": worker_name,
                "healthy": healthy,
                "state": worker["state"],
                "heartbeat_age_seconds": age,
                "cycles": int(worker.get("cycles") or 0),
                "failures": int(worker.get("failures") or 0),
            },
            sort_keys=True,
        )
    )
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
