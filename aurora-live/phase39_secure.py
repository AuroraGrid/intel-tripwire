from __future__ import annotations

import os
import urllib.parse
from typing import Any

from phase39_infrastructure import (
    CISAKEVAdapter,
    EONETWildfireAdapter,
    FEMAGovernmentAlertsAdapter,
    InfrastructureCoordinator,
    InfrastructureObservation,
    NWSAlertsAdapter,
    OFACSDNAdapter,
    RIPEBGPAdapter,
    _json,
    _now,
)


class SecureConfiguredJSONAdapter:
    endpoint = "environment-configured"
    completeness_note = "Coverage is limited to the operator-configured official feed and must not be generalized beyond that scope."
    license_note = "License must be supplied and reviewed by the operator before public redistribution."

    def __init__(self, *, name: str, layer: str, url_env: str, license_env: str, api_key_env: str = "") -> None:
        self.name = name
        self.layer = layer
        self.url_env = url_env
        self.license_env = license_env
        self.api_key_env = api_key_env

    def configured(self) -> bool:
        return bool(str(os.getenv(self.url_env) or "").strip()) and (not self.api_key_env or bool(str(os.getenv(self.api_key_env) or "").strip()))

    def configuration(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "layer": self.layer,
            "configured": self.configured(),
            "url_env": self.url_env,
            "api_key_env": self.api_key_env or None,
            "credentials_never_returned": True,
        }

    def fetch(self, timeout: int = 30) -> list[InfrastructureObservation]:
        template = str(os.getenv(self.url_env) or "").strip()
        key = str(os.getenv(self.api_key_env) or "").strip() if self.api_key_env else ""
        if not self.configured():
            raise RuntimeError(f"{self.url_env} is not configured")
        request_url = template.replace("{api_key}", urllib.parse.quote(key, safe="")) if "{api_key}" in template else template
        try:
            data = _json(request_url, timeout=timeout)
        except Exception as exc:
            raise RuntimeError(f"{self.name} request failed") from exc
        rows = data if isinstance(data, list) else (data.get("events") or data.get("outages") or data.get("data") or data.get("items") or [])
        observed_at = _now()
        output: list[InfrastructureObservation] = []
        for index, item in enumerate(rows[:500]):
            if not isinstance(item, dict):
                continue
            identifier = str(item.get("id") or item.get("event_id") or item.get("outage_id") or item.get("timestamp") or index)
            title = str(item.get("title") or item.get("name") or item.get("event") or item.get("status") or f"{self.layer} observation {identifier}")
            event_time = str(item.get("event_time") or item.get("updated_at") or item.get("timestamp") or observed_at)
            latitude = item.get("latitude") if isinstance(item.get("latitude"), (int, float)) else None
            longitude = item.get("longitude") if isinstance(item.get("longitude"), (int, float)) else None
            output.append(
                InfrastructureObservation(
                    layer=self.layer,
                    provider=self.name,
                    external_id=identifier,
                    observed_at=observed_at,
                    event_time=event_time,
                    severity=str(item.get("severity") or "UNKNOWN").upper(),
                    title=title[:500],
                    summary=str(item.get("summary") or item.get("description") or "")[:2000],
                    source_url=f"env://{self.url_env}",
                    payload=item,
                    provenance={
                        "provider": self.name,
                        "source_env": self.url_env,
                        "credential_env": self.api_key_env or None,
                        "credential_committed": False,
                        "request_url_persisted": False,
                        "license_note": str(os.getenv(self.license_env) or self.license_note),
                    },
                    latitude=float(latitude) if latitude is not None else None,
                    longitude=float(longitude) if longitude is not None else None,
                )
            )
        return output


class SecureInfrastructureCoordinator(InfrastructureCoordinator):
    def __init__(self, store) -> None:
        self.store = store
        self.adapters = [
            NWSAlertsAdapter(),
            EONETWildfireAdapter(),
            SecureConfiguredJSONAdapter(
                name="configured-official-outage-feed",
                layer="outage",
                url_env="AURORA_OUTAGE_FEED_URL",
                license_env="AURORA_OUTAGE_FEED_LICENSE",
            ),
            RIPEBGPAdapter(),
            SecureConfiguredJSONAdapter(
                name="configured-official-power-feed",
                layer="power",
                url_env="AURORA_POWER_FEED_URL",
                license_env="AURORA_POWER_FEED_LICENSE",
                api_key_env="AURORA_POWER_API_KEY",
            ),
            CISAKEVAdapter(),
            OFACSDNAdapter(),
            FEMAGovernmentAlertsAdapter(),
        ]
        self._ensure_registered()
