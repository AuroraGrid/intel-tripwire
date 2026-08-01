from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASELINE_PATH = Path(__file__).resolve().parent / "qualification" / "world_monitor_baseline_2026-07-23.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_baseline(path: Path | None = None) -> dict[str, Any]:
    target = path or BASELINE_PATH
    if not target.is_file():
        return {
            "source": "missing",
            "named_providers": 65,
            "curated_feeds": 500,
            "map_layers": 56,
            "note": "Baseline file missing; using documented public World Monitor floor values.",
        }
    return json.loads(target.read_text(encoding="utf-8"))


def gate(value: Any, threshold: Any, *, higher_is_better: bool = True) -> str:
    try:
        left = float(value)
        right = float(threshold)
    except (TypeError, ValueError):
        return "NOT_VERIFIED"
    if higher_is_better:
        if left > right:
            return "VERIFIED"
        if left >= right * 0.8:
            return "PARTIAL"
        return "NOT_VERIFIED"
    if left < right:
        return "VERIFIED"
    if left <= right * 1.2:
        return "PARTIAL"
    return "NOT_VERIFIED"


def build_benchmark_report(
    *,
    product: dict[str, Any],
    transport_health: dict[str, Any] | None = None,
    infrastructure_health: dict[str, Any] | None = None,
    markets_health: dict[str, Any] | None = None,
    ops_summary: dict[str, Any] | None = None,
    baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    baseline = baseline or load_baseline()
    capabilities = list(product.get("capabilities") or [])
    live = sum(1 for item in capabilities if item.get("status") == "LIVE")
    partial = sum(1 for item in capabilities if item.get("status") == "PARTIAL")
    providers = 0
    for health in (transport_health, infrastructure_health, markets_health):
        if health:
            providers += len(health.get("providers") or [])

    metrics = {
        "capability_live": live,
        "capability_partial": partial,
        "capability_total": len(capabilities),
        "named_providers_registered": providers,
        "ops_samples": (ops_summary or {}).get("samples") or 0,
        "ops_uptime_ratio": (ops_summary or {}).get("uptime_ratio"),
    }

    comparisons = [
        {
            "metric": "named_providers_registered",
            "aurora": providers,
            "baseline": baseline.get("named_providers") or baseline.get("providers") or 65,
            "result": gate(providers, baseline.get("named_providers") or baseline.get("providers") or 65),
        },
        {
            "metric": "capability_live_or_partial",
            "aurora": live + partial,
            "baseline": baseline.get("map_layers") or 56,
            "result": gate(live + partial, baseline.get("map_layers") or 56),
        },
        {
            "metric": "long_run_ops_samples",
            "aurora": metrics["ops_samples"],
            "baseline": 100,
            "result": gate(metrics["ops_samples"], 100),
        },
    ]

    overall = "NOT_VERIFIED"
    if all(row["result"] == "VERIFIED" for row in comparisons):
        overall = "VERIFIED"
    elif any(row["result"] in {"VERIFIED", "PARTIAL"} for row in comparisons):
        overall = "PARTIAL"

    return {
        "product": "AURORA LIVE",
        "phase": product.get("phase"),
        "overall": overall,
        "ten_of_ten": False,
        "ten_of_ten_reason": "10/10 requires independent long-run production proof and full competitive gate verification; this harness never auto-promotes that designation.",
        "metrics": metrics,
        "comparisons": comparisons,
        "baseline_source": baseline.get("source") or str(BASELINE_PATH.name),
        "generated_at": _now(),
    }
