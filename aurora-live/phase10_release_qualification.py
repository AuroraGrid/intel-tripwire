from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from phase10_assets import all_static_assets
from phase10_benchmark import qualify as performance_qualify
from phase10_catalog import country_features, overpass_assets, submarine_cables, world_bank_countries


BASELINES = {
    "chokepoint": 14,
    "cable": 87,
    "pipeline_lng": 89,
    "datacenter": 314,
    "hotspot": 30,
    "market": 93,
    "country": 196,
}


def _attempt(name: str, loader: Callable[[], Any], retries: int = 2) -> tuple[Any, dict[str, Any]]:
    started = time.perf_counter()
    error = ""
    for attempt in range(1, retries + 1):
        try:
            value = loader()
            return value, {"name": name, "status": "online", "attempts": attempt, "latency_ms": round((time.perf_counter() - started) * 1000, 3)}
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"[:500]
            if attempt < retries:
                time.sleep(1.5 * attempt)
    return None, {"name": name, "status": "degraded", "attempts": retries, "latency_ms": round((time.perf_counter() - started) * 1000, 3), "error": error}


def qualify(run_live: bool = True) -> dict[str, Any]:
    static = all_static_assets()
    counts = dict(Counter(str(row.get("type")) for row in static))
    countries, country_health = _attempt("countries", world_bank_countries)
    geometry, geometry_health = _attempt("country_geometry", country_features)
    health = [country_health, geometry_health]

    if run_live:
        cable_data, cable_health = _attempt("submarine_cables", submarine_cables)
        health.append(cable_health)
        if cable_data:
            counts["cable"] = len(cable_data.get("cables") or [])
            counts["cable_landing"] = len(cable_data.get("landing_points") or [])
        for kind in ("pipeline", "lng", "datacenter"):
            rows, row_health = _attempt(kind, lambda kind=kind: overpass_assets(kind))
            health.append(row_health)
            counts[kind] = len(rows or [])
    else:
        counts.update({"cable": 0, "pipeline": 0, "lng": 0, "datacenter": 0})

    performance = performance_qualify(objects=20000, iterations=15)
    country_count = len(countries or [])
    feature_count = len((geometry or {}).get("features") or []) if isinstance(geometry, dict) else 0
    pipeline_lng = counts.get("pipeline", 0) + counts.get("lng", 0)
    gates = {
        "webgl_and_filtering": performance.get("performance_passed", False),
        "countries": country_count >= BASELINES["country"] and feature_count >= 170,
        "chokepoints": counts.get("chokepoint", 0) >= BASELINES["chokepoint"],
        "cables": counts.get("cable", 0) >= BASELINES["cable"],
        "pipeline_lng": pipeline_lng >= BASELINES["pipeline_lng"],
        "datacenters": counts.get("datacenter", 0) >= BASELINES["datacenter"],
        "hotspots": counts.get("hotspot", 0) >= BASELINES["hotspot"],
        "markets": counts.get("market", 0) >= BASELINES["market"],
        "all_live_layers_healthy": (not run_live) or all(item["status"] == "online" for item in health if item["name"] in {"submarine_cables", "pipeline", "lng", "datacenter"}),
    }
    result = {
        "schema_version": "1.1",
        "mode": "live" if run_live else "offline",
        "baselines": BASELINES,
        "counts": {**counts, "pipeline_lng": pipeline_lng, "country": country_count, "country_features": feature_count, "static_assets": len(static)},
        "health": health,
        "performance": performance,
        "gates": gates,
        "verification_note": "Static reference layers are curated and versioned. Cable, pipeline, LNG and datacenter gates require live upstream data and cannot pass on fixture counts.",
    }
    result["passed"] = all(gates.values()) if run_live else all(value for key, value in gates.items() if key not in {"cables", "pipeline_lng", "datacenters", "all_live_layers_healthy"})
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    result = qualify(not args.offline)
    text = json.dumps(result, indent=2, sort_keys=True)
    print(text)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
