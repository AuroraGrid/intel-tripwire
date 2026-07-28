from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from phase32_product_spec import CAPABILITIES, PRIORITIES, STATUSES


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _base_items() -> list[dict[str, Any]]:
    return [deepcopy(capability.value()) for capability in CAPABILITIES]


def _evidence_status(
    key: str,
    declared_status: str,
    *,
    webcam_coverage: dict[str, Any],
    imagery_baseline: dict[str, Any],
    unified_health: dict[str, Any],
) -> tuple[str, list[str], str]:
    evidence: list[str] = []
    reason = "No Phase 37 runtime rule changes the declared status."

    if key == "webcams":
        qualified_regions = int(webcam_coverage.get("qualified_regions", 0))
        total_online = int(webcam_coverage.get("total_online", 0))
        total_registered = int(webcam_coverage.get("total_registered", 0))
        evidence = [
            f"registered={total_registered}",
            f"online={total_online}",
            f"qualified_regions={qualified_regions}/7",
            "registration is not live evidence",
        ]
        if webcam_coverage.get("fully_qualified"):
            return "LIVE", evidence, "All seven regions have at least ten independently health-verified online cameras."
        if total_registered or total_online:
            return "PARTIAL", evidence, "Durable webcam operations exist, but the 70-camera qualification gate is incomplete."
        return "PARTIAL", evidence, "The durable registry, health history and qualification matrix exist, but no cameras are currently qualified."

    if key in {"live-imagery", "satellite-imagery"}:
        qualified_regions = int(imagery_baseline.get("qualified_regions", 0))
        evidence = [
            f"qualified_regions={qualified_regions}/7",
            "validated image format and dimensions",
            "SHA-256 content identity",
            "durable observation history",
        ]
        if imagery_baseline.get("fully_qualified"):
            return "LIVE", evidence, "Every required region has a successful official-source imagery observation."
        if qualified_regions:
            return "PARTIAL", evidence, "Operational imagery exists, but the seven-region baseline is incomplete."
        return "PARTIAL", evidence, "The operational imagery pipeline exists, but this runtime has no qualifying observations yet."

    if key == "source-health":
        feeds = unified_health.get("feeds", [])
        configured = [row for row in feeds if row.get("state") != "NOT_CONFIGURED"]
        evidence = [f"configured_feeds={len(configured)}", f"aggregate_state={unified_health.get('state', 'UNKNOWN')}"]
        if configured:
            return "LIVE", evidence, "Unified source-health evaluation is active for configured feeds."
        return "PARTIAL", evidence, "The source-health engine exists, but this runtime has no configured feeds."

    return declared_status, evidence, reason


def reconciled_manifest(
    *,
    webcam_coverage: dict[str, Any],
    imagery_baseline: dict[str, Any],
    unified_health: dict[str, Any],
) -> dict[str, Any]:
    items = _base_items()
    for item in items:
        declared_status = item["status"]
        effective_status, runtime_evidence, reason = _evidence_status(
            item["key"],
            declared_status,
            webcam_coverage=webcam_coverage,
            imagery_baseline=imagery_baseline,
            unified_health=unified_health,
        )
        item["declared_status"] = declared_status
        item["status"] = effective_status
        item["status_source"] = "runtime-evidence" if effective_status != declared_status or runtime_evidence else "canonical-declaration"
        item["runtime_evidence"] = runtime_evidence
        item["qualification_reason"] = reason

    counts = {status: sum(item["status"] == status for item in items) for status in sorted(STATUSES)}
    declared_counts = {status: sum(item["declared_status"] == status for item in items) for status in sorted(STATUSES)}
    priorities = {priority: sum(item["priority"] == priority for item in items) for priority in sorted(PRIORITIES)}
    return {
        "product": "AURORA LIVE",
        "phase": 37,
        "mission": "A free global evidence and decision-intelligence operating system combining live events, media, markets, transportation, disasters, infrastructure and verified analysis.",
        "workflow": ["SCOUT", "SOURCEGRID", "K-ALIGN", "BLACKGLASS", "CRF/IPR", "COMMAND", "AURORA GRID", "RECORD LOCK"],
        "regions": ["Oceania", "Africa", "Asia", "Middle East", "Europe", "North America", "South America"],
        "interface": ["Global Operating Picture", "Incident Room", "Source Health"],
        "status_policy": {
            "declared_status": "the canonical implementation expectation",
            "effective_status": "the status supported by current qualified runtime evidence",
            "registration_is_not_evidence": True,
            "fixtures_and_ui_labels_do_not_qualify": True,
        },
        "counts": counts,
        "declared_counts": declared_counts,
        "priority_counts": priorities,
        "runtime_summary": {
            "webcams": webcam_coverage,
            "imagery": imagery_baseline,
            "source_health": unified_health,
        },
        "capabilities": items,
        "generated_at": _now(),
    }


def reconciled_gaps(
    *,
    webcam_coverage: dict[str, Any],
    imagery_baseline: dict[str, Any],
    unified_health: dict[str, Any],
    priority: str = "",
) -> dict[str, Any]:
    normalized = str(priority or "").upper()
    if normalized and normalized not in PRIORITIES:
        raise ValueError("invalid priority")
    manifest = reconciled_manifest(
        webcam_coverage=webcam_coverage,
        imagery_baseline=imagery_baseline,
        unified_health=unified_health,
    )
    open_states = {"PARTIAL", "PLANNED", "BLOCKED", "NOT_VERIFIED"}
    items = [
        item
        for item in manifest["capabilities"]
        if item["status"] in open_states and (not normalized or item["priority"] == normalized)
    ]
    items.sort(key=lambda item: (item["priority"], item["domain"], item["key"]))
    return {
        "phase": 37,
        "total": len(items),
        "priority": normalized or "ALL",
        "gaps": items,
        "generated_at": _now(),
    }
