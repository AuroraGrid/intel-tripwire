from __future__ import annotations

import argparse
import json
from pathlib import Path

from phase9_scale import catalog_summary, probe_all, scaled_registry_manifest


def qualify(run_live: bool = True, min_online: int = 50) -> dict:
    registry = scaled_registry_manifest()
    catalog = catalog_summary()
    providers = {str(item.get("provider") or item.get("name")) for item in registry}
    capabilities = {str(item.get("capability")) for item in registry if item.get("capability")}
    probes = probe_all() if run_live else []
    online = [row for row in probes if row.get("status") == "online"]
    degraded = [row for row in probes if row.get("status") != "online"]
    result = {
        "schema_version": "1.0",
        "registry": {
            "providers": len(providers),
            "curated_feeds": catalog["curated_streams"],
            "layers": catalog["layers"],
            "capability_classes": len(capabilities),
        },
        "live_probe": {
            "enabled": run_live,
            "probed": len(probes),
            "online": len(online),
            "degraded": len(degraded),
            "minimum_online": min_online,
            "degraded_providers": degraded,
        },
    }
    result["registry_gate_passed"] = (
        len(providers) > 65
        and catalog["curated_streams"] > 500
        and catalog["layers"] >= 60
        and len(capabilities) >= 15
    )
    result["live_probe_gate_passed"] = (not run_live) or len(online) >= min_online
    result["passed"] = result["registry_gate_passed"] and result["live_probe_gate_passed"]
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--min-online", type=int, default=50)
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    result = qualify(not args.offline, args.min_online)
    text = json.dumps(result, indent=2, sort_keys=True)
    print(text)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
