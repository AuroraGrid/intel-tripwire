from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from phase32_product_spec import PRIORITIES, STATUSES
from phase39_capabilities import reconciled_manifest as phase39_manifest


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _rows(health: dict[str, Any], domain: str) -> tuple[list[dict[str, Any]], bool]:
    rows = [row for row in health.get("providers") or [] if row.get("domain") == domain]
    return rows, any(bool(row.get("operational")) for row in rows)


def _evidence(rows: list[dict[str, Any]]) -> list[str]:
    output = []
    for row in rows:
        output.append(
            f"{row.get('provider')}: state={row.get('state')}, operational={bool(row.get('operational'))}, "
            f"observations={int(row.get('observations') or 0)}, event_age_seconds={int(row.get('event_age_seconds') or 0)}"
        )
    return output or ["no provider evidence recorded"]


def _set(item: dict[str, Any], operational: bool, rows: list[dict[str, Any]], domain: str) -> None:
    status = "LIVE" if operational else "PARTIAL"
    if status not in STATUSES:
        raise ValueError("invalid capability status")
    item["declared_status"] = item.get("declared_status", item.get("status", "NOT_VERIFIED"))
    item["status"] = status
    item["status_source"] = "runtime-evidence"
    item["runtime_evidence"] = _evidence(rows)
    item["qualification_reason"] = (
        f"The {domain} domain has a recent successful provider retrieval and durable numeric observations."
        if operational
        else f"The {domain} implementation exists, but current runtime evidence does not satisfy the operational qualification gate."
    )


def reconciled_manifest(
    *,
    webcam_coverage: dict[str, Any],
    imagery_baseline: dict[str, Any],
    unified_health: dict[str, Any],
    transport_health: dict[str, Any],
    infrastructure_health: dict[str, Any],
    market_health: dict[str, Any],
) -> dict[str, Any]:
    base = phase39_manifest(
        webcam_coverage=webcam_coverage,
        imagery_baseline=imagery_baseline,
        unified_health=unified_health,
        transport_health=transport_health,
        infrastructure_health=infrastructure_health,
    )
    manifest = deepcopy(base)
    items = list(manifest["capabilities"])
    by_key = {item["key"]: item for item in items}
    rules = {
        "global-stocks": "equities",
        "energy": "energy",
        "commodities": "commodities",
        "currencies": "fx",
        "crypto": "crypto",
        "economic-indicators": "economic_indicators",
        "prediction-markets": "prediction_markets",
    }
    for key, domain in rules.items():
        rows, operational = _rows(market_health, domain)
        _set(by_key[key], operational, rows, domain)

    counts = {status: sum(item["status"] == status for item in items) for status in sorted(STATUSES)}
    declared_counts = {status: sum(item.get("declared_status") == status for item in items) for status in sorted(STATUSES)}
    priorities = {priority: sum(item["priority"] == priority for item in items) for priority in sorted(PRIORITIES)}
    manifest.update(
        {
            "phase": 40,
            "counts": counts,
            "declared_counts": declared_counts,
            "priority_counts": priorities,
            "runtime_summary": {**dict(manifest.get("runtime_summary") or {}), "markets": market_health},
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
    market_health: dict[str, Any],
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
        market_health=market_health,
    )
    open_states = {"PARTIAL", "PLANNED", "BLOCKED", "NOT_VERIFIED"}
    items = [
        item
        for item in manifest["capabilities"]
        if item["status"] in open_states and (not normalized or item["priority"] == normalized)
    ]
    items.sort(key=lambda item: (item["priority"], item["domain"], item["key"]))
    return {"phase": 40, "total": len(items), "priority": normalized or "ALL", "gaps": items, "generated_at": _now()}
