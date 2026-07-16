from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from phase10_assets import all_static_assets
from phase10_benchmark import qualify as performance_qualify
from phase10_catalog import country_features, world_bank_countries


BASELINES = {
    "chokepoint": 14,
    "cable": 87,
    "pipeline_lng": 89,
    "datacenter": 314,
    "hotspot": 30,
    "market": 93,
    "country": 196,
}

OVERPASS_ENDPOINTS = (
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.nchc.org.tw/api/interpreter",
)


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


def _request_json(url: str, body: bytes | None = None, timeout: int = 45) -> Any:
    request = urllib.request.Request(
        url,
        data=body,
        method="POST" if body is not None else "GET",
        headers={
            "User-Agent": "AURORA-LIVE/1.0 (+mailto:hr185882@gmail.com)",
            "Accept": "application/json, text/json, */*;q=0.1",
            "Accept-Encoding": "identity",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8-sig", "replace").strip()
        if not raw.startswith(("[", "{")):
            raise ValueError(f"non-JSON response from {response.geturl()}")
        return json.loads(raw)


def _cable_counts() -> dict[str, int]:
    errors = []
    for attempt in range(3):
        suffix = "" if attempt == 0 else f"?qualification={int(time.time())}-{attempt}"
        try:
            cables = _request_json("https://www.submarinecablemap.com/api/v3/cable/all.json" + suffix, timeout=30)
            landings = _request_json("https://www.submarinecablemap.com/api/v3/landing-point/all.json" + suffix, timeout=30)
            cable_rows = cables if isinstance(cables, list) else cables.get("cables", [])
            landing_rows = landings if isinstance(landings, list) else landings.get("landing_points", [])
            return {"cable": len(cable_rows), "cable_landing": len(landing_rows)}
        except Exception as exc:
            errors.append(str(exc))
            time.sleep(1.0 + attempt)
    raise RuntimeError("; ".join(errors)[:500])


def _overpass_count(kind: str) -> int:
    selectors = {
        "datacenter": '(nwr["man_made"="data_centre"];nwr["telecom"="data_center"];nwr["telecom"="data_centre"];);',
        "pipeline": '(nwr["man_made"="pipeline"]["substance"~"gas|oil|petroleum|lng",i];);',
        "lng": '(nwr["industrial"="terminal"]["product"~"lng|natural_gas",i];nwr["man_made"="storage_tank"]["content"~"lng",i];);',
    }
    if kind not in selectors:
        raise ValueError("unsupported infrastructure count")
    query = f"[out:json][timeout:60];{selectors[kind]}out count;"
    body = urllib.parse.urlencode({"data": query}).encode("ascii")
    errors = []
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            data = _request_json(endpoint, body, timeout=75)
            elements = data.get("elements") or []
            tags = (elements[0].get("tags") or {}) if elements else {}
            count = int(tags.get("total") or 0)
            if count < 1:
                raise ValueError(f"empty count from {endpoint}")
            return count
        except Exception as exc:
            errors.append(f"{endpoint}: {exc}")
    raise RuntimeError("; ".join(errors)[:700])


def qualify(run_live: bool = True) -> dict[str, Any]:
    static = all_static_assets()
    counts = dict(Counter(str(row.get("type")) for row in static))
    countries, country_health = _attempt("countries", world_bank_countries)
    geometry, geometry_health = _attempt("country_geometry", country_features)
    health = [country_health, geometry_health]

    if run_live:
        cable_counts, cable_health = _attempt("submarine_cables", _cable_counts, retries=1)
        health.append(cable_health)
        counts.update(cable_counts or {})
        for kind in ("pipeline", "lng", "datacenter"):
            value, row_health = _attempt(kind, lambda kind=kind: _overpass_count(kind), retries=1)
            health.append(row_health)
            counts[kind] = int(value or 0)
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
        "schema_version": "1.2",
        "mode": "live" if run_live else "offline",
        "baselines": BASELINES,
        "counts": {**counts, "pipeline_lng": pipeline_lng, "country": country_count, "country_features": feature_count, "static_assets": len(static)},
        "health": health,
        "performance": performance,
        "gates": gates,
        "verification_note": "Static reference layers are curated and versioned. Cable counts come from Submarine Cable Map. Pipeline/LNG and datacenter counts use live count-only OpenStreetMap Overpass queries; fixture counts cannot satisfy these gates.",
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
