from __future__ import annotations

from collections import Counter
from typing import Any

import app
from phase9_scale import catalog_summary, scaled_phase9_adapters, scaled_registry_manifest
from release_engine import ReleaseAggregator


class Phase9Aggregator(ReleaseAggregator):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("adapter_factory", scaled_phase9_adapters)
        super().__init__(*args, **kwargs)

    def collect(self, query: str = app.DEFAULT_QUERY, force: bool = False):
        payload = super().collect(query, force)
        registry = scaled_registry_manifest(query)
        registry_by_name = {item["name"]: item for item in registry}
        for source in payload.get("sources") or []:
            metadata = registry_by_name.get(source.get("source"), {})
            source.update({key: value for key, value in metadata.items() if key not in {"name", "runtime"}})
            source["runtime"] = metadata.get("runtime", {})
        providers = {item.get("provider") or item.get("name") for item in registry if item.get("provider") or item.get("name")}
        capabilities = {item.get("capability") for item in registry if item.get("capability")}
        online = [item for item in payload.get("sources") or [] if item.get("status") == "online"]
        degraded = [item for item in payload.get("sources") or [] if item.get("status") != "online"]
        catalog = catalog_summary()
        payload["source_registry"] = registry
        payload["feed_catalog"] = catalog
        payload["registry_totals"] = {
            "adapters": len(registry),
            "providers": len(providers),
            "curated_feeds": catalog["curated_streams"],
            "layers": catalog["layers"],
            "capability_classes": len(capabilities),
            "official_adapters": sum(1 for item in registry if item.get("official")),
        }
        payload["live_totals"] = {
            "online_sources": len(online),
            "degraded_sources": len(degraded),
            "online_providers": len({item.get("provider") or item.get("source") for item in online}),
            "online_capability_classes": len({item.get("capability") for item in online if item.get("capability")}),
            "records_by_capability": dict(Counter(item.get("capability") for item in online if item.get("capability"))),
        }
        payload["phase9_gate"] = {
            "registry_breadth_passed": len(providers) > 65 and catalog["curated_streams"] > 500 and catalog["layers"] >= 60 and len(capabilities) >= 15,
            "live_core_passed": len(online) >= 30 and payload["live_totals"]["online_capability_classes"] >= 15,
            "source_repair_passed": not any(item.get("source") in {"AWC METAR", "AWC TAF", "ECDC News", "ENISA Cybersecurity News", "IAEA News", "NATO News", "IFRC Humanitarian News", "UN News Humanitarian", "WHO Disease Outbreak News"} for item in degraded),
            "target_providers": 66,
            "target_curated_feeds": 501,
            "target_layers": 60,
            "target_capability_classes": 15,
            "qualification_note": "Registry breadth is distinct from simultaneous polling. The core live set runs every cycle; additional providers are health-probed and polled in rotating batches.",
        }
        return payload


def registry_summary(query: str = app.DEFAULT_QUERY) -> dict[str, Any]:
    registry = scaled_registry_manifest(query)
    catalog = catalog_summary()
    return {
        "sources": registry,
        "totals": {
            "adapters": len(registry),
            "providers": len({item.get("provider") or item.get("name") for item in registry}),
            "curated_feeds": catalog["curated_streams"],
            "layers": catalog["layers"],
            "capability_classes": len({item.get("capability") for item in registry if item.get("capability")}),
        },
    }
