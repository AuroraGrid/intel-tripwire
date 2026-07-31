from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from phase32_product_spec import PRIORITIES, STATUSES
from phase37_capabilities import reconciled_manifest as phase37_manifest


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _provider_summary(health: dict[str, Any], *, domain: str = "", layer: str = "") -> tuple[list[dict[str, Any]], bool]:
    providers = list(health.get("providers") or [])
    if domain:
        providers = [row for row in providers if row.get("domain") == domain]
    if layer:
        providers = [row for row in providers if row.get("layer") == layer]
    return providers, any(bool(row.get("operational")) for row in providers)


def _runtime_evidence(rows: list[dict[str, Any]]) -> list[str]:
    evidence = []
    for row in rows:
        name = str(row.get("provider") or "unknown")
        state = str(row.get("state") or "UNKNOWN")
        operational = bool(row.get("operational"))
        observations = int(row.get("observations") or 0)
        evidence.append(f"{name}: state={state}, operational={operational}, observations={observations}")
    return evidence or ["no provider evidence recorded"]


def _set_runtime_status(item: dict[str, Any], *, status: str, evidence: list[str], reason: str) -> None:
    if status not in STATUSES:
        raise ValueError("invalid capability status")
    item["declared_status"] = item.get("declared_status", item.get("status", "NOT_VERIFIED"))
    item["status"] = status
    item["status_source"] = "runtime-evidence"
    item["runtime_evidence"] = evidence
    item["qualification_reason"] = reason


def reconciled_manifest(
    *,
    webcam_coverage: dict[str, Any],
    imagery_baseline: dict[str, Any],
    unified_health: dict[str, Any],
    transport_health: dict[str, Any],
    infrastructure_health: dict[str, Any],
) -> dict[str, Any]:
    base = phase37_manifest(
        webcam_coverage=webcam_coverage,
        imagery_baseline=imagery_baseline,
        unified_health=unified_health,
    )
    manifest = deepcopy(base)
    items = list(manifest["capabilities"])
    by_key = {item["key"]: item for item in items}

    transport_rules = {
        "aviation": "aviation",
        "maritime": "maritime",
    }
    for key, domain in transport_rules.items():
        rows, operational = _provider_summary(transport_health, domain=domain)
        _set_runtime_status(
            by_key[key],
            status="LIVE" if operational else "PARTIAL",
            evidence=_runtime_evidence(rows),
            reason=(
                f"The {domain} layer has a recent successful provider run and fresh, durably persisted observations."
                if operational
                else f"The {domain} adapter and persistence path exist, but current runtime evidence does not satisfy the operational qualification gate."
            ),
        )

    layer_rules = {
        "weather": "severe_weather",
        "wildfires": "wildfire",
        "internet-outages": "outage",
        "bgp": "bgp",
        "power-outages": "power",
        "cyberalerts": "cyber",
        "sanctions": "sanctions",
        "government-alerts": "government_alerts",
    }
    for key, layer in layer_rules.items():
        rows, operational = _provider_summary(infrastructure_health, layer=layer)
        _set_runtime_status(
            by_key[key],
            status="LIVE" if operational else "PARTIAL",
            evidence=_runtime_evidence(rows),
            reason=(
                f"The {layer} layer has a recent successful retrieval and fresh, durably persisted observations."
                if operational
                else f"The {layer} implementation exists, but current runtime evidence does not satisfy the operational qualification gate."
            ),
        )

    infrastructure_rows = list(infrastructure_health.get("providers") or [])
    infrastructure_complete = bool(infrastructure_health.get("fully_operational"))
    _set_runtime_status(
        by_key["infrastructure"],
        status="LIVE" if infrastructure_complete else "PARTIAL",
        evidence=[
            f"operational_layers={int(infrastructure_health.get('operational_layers') or 0)}/8",
            *_runtime_evidence(infrastructure_rows),
        ],
        reason=(
            "All eight Phase 39 infrastructure-risk layers satisfy the runtime qualification gate."
            if infrastructure_complete
            else "The Phase 39 infrastructure engine is operational, but one or more layers remain unqualified or not configured."
        ),
    )

    counts = {status: sum(item["status"] == status for item in items) for status in sorted(STATUSES)}
    declared_counts = {status: sum(item.get("declared_status") == status for item in items) for status in sorted(STATUSES)}
    priorities = {priority: sum(item["priority"] == priority for item in items) for priority in sorted(PRIORITIES)}
    manifest.update(
        {
            "phase": 39,
            "counts": counts,
            "declared_counts": declared_counts,
            "priority_counts": priorities,
            "runtime_summary": {
                **dict(manifest.get("runtime_summary") or {}),
                "transport": transport_health,
                "infrastructure": infrastructure_health,
            },
            "generated_at": _now(),
        }
    )
    return manifest


def reconciled_gaps(
    *,
    webcam_coverage: dict[str, Any],
    imagery_baseline: dict[str, Any],
    unified_health: dict[str, Any],
    transport_health: dict[str, Any],
    infrastructure_health: dict[str, Any],
    priority: str = "",
) -> dict[str, Any]:
    normalized = str(priority or "").upper()
    if normalized and normalized not in PRIORITIES:
        raise ValueError("invalid priority")
    manifest = reconciled_manifest(
        webcam_coverage=webcam_coverage,
        imagery_baseline=imagery_baseline,
        unified_health=unified_health,
        transport_health=transport_health,
        infrastructure_health=infrastructure_health,
    )
    open_states = {"PARTIAL", "PLANNED", "BLOCKED", "NOT_VERIFIED"}
    items = [
        item
        for item in manifest["capabilities"]
        if item["status"] in open_states and (not normalized or item["priority"] == normalized)
    ]
    items.sort(key=lambda item: (item["priority"], item["domain"], item["key"]))
    return {
        "phase": 39,
        "total": len(items),
        "priority": normalized or "ALL",
        "gaps": items,
        "generated_at": _now(),
    }
