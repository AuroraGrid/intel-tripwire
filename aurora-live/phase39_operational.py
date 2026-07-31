from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from phase39_infrastructure import InfrastructureStore, LAYERS, _now, _parse_time


class OperationalInfrastructureStore(InfrastructureStore):
    """Qualification uses fresh retrieval evidence while retaining event-age telemetry."""

    def health(self, max_age_seconds: int | None = None) -> dict[str, Any]:
        maximum_age = max(60, int(max_age_seconds or os.getenv("AURORA_INFRASTRUCTURE_STALE_SECONDS", "3600")))
        now = datetime.now(timezone.utc)
        provider_health: list[dict[str, Any]] = []
        for provider in self.providers():
            last_success = _parse_time(provider.get("last_success_at"))
            age = int((now - last_success).total_seconds()) if last_success else None
            observations = self.observations(provider=provider["provider"], limit=1)
            runs = self.runs(provider=provider["provider"], limit=1)
            latest_run = runs[0] if runs else None
            fresh = bool(provider["state"] == "ONLINE" and age is not None and age <= maximum_age)
            operational = bool(fresh and observations and latest_run and latest_run["successful"] and latest_run["observations"] > 0)
            provider_health.append(
                {
                    **provider,
                    "seconds_since_success": age,
                    "retrieval_fresh": fresh,
                    "event_freshness_seconds": max(0, int(provider.get("freshness_seconds") or 0)),
                    "fresh": fresh,
                    "operational": operational,
                    "observations": 1 if observations else 0,
                    "latest_observation": observations[0] if observations else None,
                    "latest_run": latest_run,
                }
            )
        layers = []
        for layer in LAYERS:
            rows = [row for row in provider_health if row["layer"] == layer]
            layers.append(
                {
                    "layer": layer,
                    "providers": len(rows),
                    "operational_providers": sum(1 for row in rows if row["operational"]),
                    "operational": any(row["operational"] for row in rows),
                }
            )
        return {
            "max_age_seconds": maximum_age,
            "freshness_basis": "recent successful provider retrieval with durable observations; event age is reported separately",
            "providers": provider_health,
            "layers": layers,
            "operational_layers": sum(1 for row in layers if row["operational"]),
            "fully_operational": all(row["operational"] for row in layers),
            "generated_at": _now(),
        }
