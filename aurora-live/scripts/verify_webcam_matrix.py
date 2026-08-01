from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from phase37_webcams import DurableWebcamRegistry, WebcamHealthCoordinator, WebcamStore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify webcam matrix health evidence")
    parser.add_argument(
        "--database",
        default=os.getenv("AURORA_WEBCAM_DB") or os.getenv("AURORA_OPERATIONAL_DB") or "var/webcams.sqlite3",
    )
    parser.add_argument("--region", default="")
    parser.add_argument("--limit", type=int, default=250)
    parser.add_argument("--probe", action="store_true", help="Run live HTTP probes (network required)")
    args = parser.parse_args()

    store = WebcamStore(args.database)
    registry = DurableWebcamRegistry(store)
    coordinator = WebcamHealthCoordinator(registry)

    result = None
    if args.probe:
        result = coordinator.run(region=args.region, limit=args.limit)

    payload = {
        "coverage": registry.coverage(),
        "matrix": registry.matrix(),
        "source_health": registry.source_health(),
        "probe": result,
        "fully_qualified": registry.coverage()["fully_qualified"],
        "note": "ONLINE requires stream-specific verification; registration alone is never LIVE evidence.",
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["fully_qualified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
