from __future__ import annotations

from collections import Counter
from typing import Any

import app
from phase9_sources import phase9_adapters, registry_manifest
from release_engine import ReleaseAggregator


class Phase9Aggregator(ReleaseAggregator):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("adapter_factory", phase9_adapters)
        super().__init__(*args, **kwargs)

    def collect(self, query: str = app.DEFAULT_QUERY, force: bool = False):
        payload = super().collect(query, force)
        registry = registry_manifest(query)
        registry_by_name = {item["name"]: item for item in registry}
        for source in payload.get("sources") or []:
            source.update({key: value for key, value in registry_by_name.get(source.get("source"), {}).items() if key not in {"name", "runtime"}})
            source["runtime"] = registry_by_name.get(source.get("source"), {}).get("runtime", {})
        providers = {item.get("provider") for item in registry if item.get("provider")}
        capabilities = {item.get("capability") for item in registry if item.get("capability")}
        online = [item for item in payload.get("sources") or [] if item.get("status") == "online"]
        payload["source_registry"] = registry
        payload["registry_totals"] = {
            "adapters": len(registry),
            "providers": len(providers),
            "capability_classes": len(capabilities),
            "official_adapters": sum(1 for item in registry if item.get("official")),
        }
        payload["live_totals"] = {
            "online_sources": len(online),
            "online_providers": len({item.get("provider") for item in online if item.get("provider")}),
            "online_capability_classes": len({item.get("capability") for item in online if item.get("capability")}),
            "records_by_capability": dict(Counter(item.get("capability") for item in online if item.get("capability"))),
        }
        payload["phase9_gate"] = {
            "registry_breadth_passed": len(registry) >= 25 and len(capabilities) >= 8,
            "live_breadth_passed": len(online) >= 12 and payload["live_totals"]["online_capability_classes"] >= 8,
            "target_adapters": 25,
            "target_capability_classes": 8,
        }
        return payload


def registry_summary(query: str = app.DEFAULT_QUERY) -> dict[str, Any]:
    registry = registry_manifest(query)
    return {
        "sources": registry,
        "totals": {
            "adapters": len(registry),
            "providers": len({item.get("provider") for item in registry if item.get("provider")}),
            "capability_classes": len({item.get("capability") for item in registry if item.get("capability")}),
        },
    }
