from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from phase37_webcams import DurableWebcamRegistry, WebcamStore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the AURORA LIVE regional webcam registry")
    parser.add_argument(
        "--database",
        default=os.getenv("AURORA_WEBCAM_DB") or os.getenv("AURORA_OPERATIONAL_DB") or "var/webcams.sqlite3",
    )
    parser.add_argument(
        "--manifest",
        default=str(ROOT / "fixtures" / "webcam_seed_manifest.json"),
    )
    parser.add_argument("--limit", type=int, default=0, help="Optional max cameras to seed")
    args = parser.parse_args()

    path = Path(args.manifest)
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise SystemExit("manifest must be a JSON array")
    if args.limit:
        rows = rows[: max(1, args.limit)]

    store = WebcamStore(args.database)
    registry = DurableWebcamRegistry(store)
    seeded = []
    errors = []
    for index, row in enumerate(rows):
        try:
            item = registry.register(row if isinstance(row, dict) else {})
            seeded.append(item["webcam_id"])
        except Exception as exc:
            errors.append({"index": index, "error": f"{type(exc).__name__}: {exc}", "row": row})

    coverage = registry.coverage()
    payload = {
        "manifest": str(path),
        "seeded": len(seeded),
        "errors": errors,
        "coverage": coverage,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
