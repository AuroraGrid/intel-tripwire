from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from phase32_product_spec import PRIORITIES, STATUSES
from phase40_capabilities import reconciled_manifest as phase40_manifest


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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
    markets_health: dict[str, Any],
    replay_coverage: dict[str, Any],
    media_coverage: dict[str, Any],
) -> dict[str, Any]:
    base = phase40_manifest(
        webcam_coverage=webcam_coverage,
        imagery_baseline=imagery_baseline,
        unified_health=unified_health,
        transport_health=transport_health,
        infrastructure_health=infrastructure_health,
        markets_health=markets_health,
    )
    manifest = deepcopy(base)
    items = list(manifest["capabilities"])
    by_key = {item["key"]: item for item in items}

    total_replay = int(replay_coverage.get("total") or 0)
    domain_counts = replay_coverage.get("counts") or {}
    multi_domain = sum(1 for count in domain_counts.values() if int(count or 0) > 0) >= 2
    if "event-replay" in by_key:
        _set_runtime_status(
            by_key["event-replay"],
            status="LIVE" if total_replay > 0 and multi_domain else "PARTIAL",
            evidence=[
                f"total_records={total_replay}",
                f"domains_with_data={sum(1 for count in domain_counts.values() if int(count or 0) > 0)}",
            ],
            reason=(
                "Unified multi-domain replay ledger contains durable records and supports time-window queries."
                if total_replay > 0 and multi_domain
                else "Replay infrastructure exists, but multi-domain durable history is incomplete."
            ),
        )

    media_total = int(media_coverage.get("total") or 0)
    media_counts = media_coverage.get("counts") or {}
    hashed = int(media_counts.get("HASHED") or 0) + int(media_counts.get("DUPLICATE_OF") or 0)
    if "live-imagery" in by_key:
        _set_runtime_status(
            by_key["live-imagery"],
            status="PARTIAL",
            evidence=[f"media_assets={media_total}", f"hashed_or_duplicate={hashed}"],
            reason="Media intake and lineage exist; full rights and live availability controls remain partial.",
        )
    if "video-verification" in by_key:
        _set_runtime_status(
            by_key["video-verification"],
            status="PARTIAL" if media_total else "PLANNED",
            evidence=[
                f"media_assets={media_total}",
                "authenticity_never_claimed_from_hash_alone=true",
            ],
            reason=(
                "Deterministic hash/lineage verification pipeline is available; forensic authenticity is not claimed."
                if media_total
                else "Verification pipeline is implemented but no media assets have been processed yet."
            ),
        )

    counts = {status: sum(item["status"] == status for item in items) for status in sorted(STATUSES)}
    priorities = {priority: sum(item["priority"] == priority for item in items) for priority in sorted(PRIORITIES)}
    manifest.update(
        {
            "phase": 41,
            "counts": counts,
            "priority_counts": priorities,
            "runtime_summary": {
                **dict(manifest.get("runtime_summary") or {}),
                "replay": replay_coverage,
                "media": media_coverage,
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
    markets_health: dict[str, Any],
    replay_coverage: dict[str, Any],
    media_coverage: dict[str, Any],
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
        markets_health=markets_health,
        replay_coverage=replay_coverage,
        media_coverage=media_coverage,
    )
    open_states = {"PARTIAL", "PLANNED", "BLOCKED", "NOT_VERIFIED"}
    items = [
        item
        for item in manifest["capabilities"]
        if item["status"] in open_states and (not normalized or item["priority"] == normalized)
    ]
    items.sort(key=lambda item: (item["priority"], item["domain"], item["key"]))
    return {
        "phase": 41,
        "total": len(items),
        "priority": normalized or "ALL",
        "gaps": items,
        "generated_at": _now(),
    }
