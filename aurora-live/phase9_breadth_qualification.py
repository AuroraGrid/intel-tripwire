from __future__ import annotations

import argparse
import json
from pathlib import Path

from phase9_final_repairs import final_registry_manifest
from phase9_scale import catalog_summary, probe_all, scaled_registry_manifest


def qualify(run_live: bool = True, min_expansion_online: int = 41) -> dict:
    registry = scaled_registry_manifest()
    core_registry = final_registry_manifest()
    catalog = catalog_summary()
    providers = {str(item.get("provider") or item.get("name")) for item in registry}
    core_providers = {str(item.get("provider") or item.get("name")) for item in core_registry}
    capabilities = {str(item.get("capability")) for item in registry if item.get("capability")}
    probes = probe_all() if run_live else []
    online = [row for row in probes if row.get("status") == "online"]
    degraded = [row for row in probes if row.get("status") != "online"]
    combined_qualified = len(core_providers) + len(online)
    result = {
        "schema_version": "1.1",
        "registry": {
            "providers": len(providers),
            "core_providers": len(core_providers),
            "curated_feeds": catalog["curated_streams"],
            "layers": catalog["layers"],
            "capability_classes": len(capabilities),
        },
        "live_probe": {
            "enabled": run_live,
            "probed_expansion_providers": len(probes),
            "expansion_online": len(online),
            "expansion_degraded": len(degraded),
            "minimum_expansion_online": min_expansion_online,
            "combined_core_and_expansion_qualified": combined_qualified,
            "target_combined_providers": 66,
            "degraded_providers": degraded,
        },
        "verification_note": "The core provider set is requalified by the separate AURORA Live Qualification workflow. This report probes the expansion set and combines it with that required core gate; both workflows must pass on the same commit.",
    }
    result["registry_gate_passed"] = (
        len(providers) > 65
        and catalog["curated_streams"] > 500
        and catalog["layers"] >= 60
        and len(capabilities) >= 15
    )
    result["live_probe_gate_passed"] = (not run_live) or (
        len(online) >= min_expansion_online and combined_qualified > 65
    )
    result["passed"] = result["registry_gate_passed"] and result["live_probe_gate_passed"]
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--min-expansion-online", type=int, default=41)
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    result = qualify(not args.offline, args.min_expansion_online)
    text = json.dumps(result, indent=2, sort_keys=True)
    print(text)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
