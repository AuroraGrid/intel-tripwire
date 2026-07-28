from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from phase38_transport import TransportObservation, TransportRegistry, TransportStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _freshness_seconds(observations: Iterable[TransportObservation]) -> int:
    current = datetime.now(timezone.utc)
    event_times = [_parse_time(row.event_time) for row in observations]
    valid = [value for value in event_times if value is not None]
    if not valid:
        return 0
    return max(0, int((current - max(valid)).total_seconds()))


@dataclass(frozen=True)
class ProviderRun:
    provider: str
    domain: str
    successful: bool
    observations: int
    error: str = ""
    started_at: str = ""
    completed_at: str = ""
    duration_ms: int = 0

    def value(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "domain": self.domain,
            "successful": self.successful,
            "observations": self.observations,
            "error": self.error,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_ms": self.duration_ms,
        }


class AviationWeatherAdapter:
    """Keyless official AviationWeather.gov METAR ingestion."""

    name = "aviationweather-gov"
    domain = "aviation"
    endpoint = "https://aviationweather.gov/api/data/metar"
    license_note = "Official U.S. government aviation weather data; attribution and API usage limits apply."
    completeness_note = "METAR coverage depends on reporting stations and is not a complete aircraft-position feed."

    def __init__(self, opener: Callable[..., Any] | None = None) -> None:
        self.opener = opener or urllib.request.urlopen

    def fetch(self, *, bbox: str = "-90,-180,90,180", hours: int = 1, timeout: int = 20) -> list[dict[str, Any]]:
        query = urllib.parse.urlencode({"format": "json", "bbox": bbox, "hours": max(1, min(int(hours), 24))})
        request = urllib.request.Request(
            f"{self.endpoint}?{query}",
            headers={"User-Agent": "AURORA-LIVE/1.0 (+https://github.com/hr185882-creator/intel-tripwire)"},
        )
        with self.opener(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, list):
            raise ValueError("AviationWeather.gov returned a non-list payload")
        return [row for row in payload if isinstance(row, dict)]

    def observations(self, rows: Iterable[dict[str, Any]]) -> list[TransportObservation]:
        observed_at = _now()
        output: list[TransportObservation] = []
        for row in rows:
            station = str(row.get("icaoId") or row.get("stationId") or "").strip()
            latitude = row.get("lat")
            longitude = row.get("lon")
            if not station or latitude is None or longitude is None:
                continue
            event_time = str(row.get("reportTime") or row.get("obsTime") or observed_at)
            output.append(
                TransportObservation(
                    domain=self.domain,
                    provider=self.name,
                    external_id=f"metar:{station}:{event_time}",
                    observed_at=observed_at,
                    event_time=event_time,
                    latitude=float(latitude),
                    longitude=float(longitude),
                    state="FRESH",
                    payload=row,
                    provenance={
                        "source": self.endpoint,
                        "provider": "AviationWeather.gov",
                        "key_required": False,
                        "license_note": self.license_note,
                    },
                )
            )
        return output


class AISStreamAdapter:
    """AISStream WebSocket adapter. The credential is read only from the environment."""

    name = "aisstream"
    domain = "maritime"
    endpoint = "wss://stream.aisstream.io/v0/stream"
    env_key = "AURORA_AISSTREAM_API_KEY"
    license_note = "AISStream beta service terms apply; no SLA and redistribution rights must be reviewed."
    completeness_note = "AIS reception is provider-dependent and does not imply complete global vessel coverage."

    def __init__(self, connection_factory: Callable[..., Any] | None = None) -> None:
        self.connection_factory = connection_factory

    def api_key(self) -> str:
        return str(os.getenv(self.env_key) or "").strip()

    def configured(self) -> bool:
        return bool(self.api_key())

    def _connect(self, timeout: int):
        if self.connection_factory is not None:
            return self.connection_factory(self.endpoint, timeout=timeout)
        try:
            from websocket import create_connection
        except ImportError as exc:
            raise RuntimeError("websocket-client is required for AISStream ingestion") from exc
        return create_connection(self.endpoint, timeout=timeout)

    def fetch(self, *, max_messages: int = 25, timeout: int = 20) -> list[dict[str, Any]]:
        key = self.api_key()
        if not key:
            raise RuntimeError(f"{self.env_key} is not configured")
        subscription = {
            "APIKey": key,
            "BoundingBoxes": [[[-90.0, -180.0], [90.0, 180.0]]],
            "FilterMessageTypes": ["PositionReport", "StandardClassBPositionReport", "ExtendedClassBPositionReport"],
        }
        connection = self._connect(timeout)
        rows: list[dict[str, Any]] = []
        try:
            connection.send(json.dumps(subscription, separators=(",", ":")))
            for _ in range(max(1, min(int(max_messages), 500))):
                message = json.loads(connection.recv())
                if isinstance(message, dict):
                    rows.append(message)
        finally:
            connection.close()
        return rows

    def observations(self, rows: Iterable[dict[str, Any]]) -> list[TransportObservation]:
        observed_at = _now()
        output: list[TransportObservation] = []
        for row in rows:
            metadata = row.get("MetaData") if isinstance(row.get("MetaData"), dict) else {}
            message = row.get("Message") if isinstance(row.get("Message"), dict) else {}
            latitude = metadata.get("latitude")
            longitude = metadata.get("longitude")
            mmsi = metadata.get("MMSI") or metadata.get("Mmsi")
            if latitude is None or longitude is None or not mmsi:
                continue
            event_time = str(metadata.get("time_utc") or observed_at)
            output.append(
                TransportObservation(
                    domain=self.domain,
                    provider=self.name,
                    external_id=f"ais:{mmsi}:{event_time}",
                    observed_at=observed_at,
                    event_time=event_time,
                    latitude=float(latitude),
                    longitude=float(longitude),
                    state="FRESH",
                    payload={"metadata": metadata, "message": message, "message_type": row.get("MessageType")},
                    provenance={
                        "source": self.endpoint,
                        "provider": "AISStream",
                        "credential_env": self.env_key,
                        "credential_committed": False,
                        "license_note": self.license_note,
                    },
                )
            )
        return output


class TransportProviderCoordinator:
    def __init__(self, store: TransportStore) -> None:
        self.store = store
        self.registry = TransportRegistry(store)
        self.aviation = AviationWeatherAdapter()
        self.maritime = AISStreamAdapter()
        self._ensure_registered()

    def _ensure_registered(self) -> None:
        existing = {row["provider"] for row in self.store.providers()}
        for adapter in (self.aviation, self.maritime):
            if adapter.name not in existing:
                self.registry.register_provider(
                    {
                        "provider": adapter.name,
                        "domain": adapter.domain,
                        "completeness_note": adapter.completeness_note,
                        "license_note": adapter.license_note,
                    }
                )

    def _complete_run(
        self,
        adapter: Any,
        started_at: str,
        started_clock: float,
        observations: list[TransportObservation],
        error: str = "",
    ) -> ProviderRun:
        completed_at = _now()
        duration_ms = max(0, int((time.monotonic() - started_clock) * 1000))
        successful = not error and bool(observations)
        final_error = error or ("provider returned no valid observations" if not observations else "")
        freshness = _freshness_seconds(observations)
        if successful:
            for observation in observations:
                self.store.record(observation)
        self.registry.observe_provider(
            adapter.name,
            {
                "successful": successful,
                "observed_at": completed_at,
                "freshness_seconds": freshness,
                "error": final_error,
            },
        )
        run = ProviderRun(
            provider=adapter.name,
            domain=adapter.domain,
            successful=successful,
            observations=len(observations),
            error=final_error,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
        )
        self.store.record_provider_run({**run.value(), "metadata": {"freshness_seconds": freshness}})
        return run

    def run_aviation(self, **kwargs: Any) -> ProviderRun:
        started_at = _now()
        started_clock = time.monotonic()
        try:
            observations = self.aviation.observations(self.aviation.fetch(**kwargs))
            return self._complete_run(self.aviation, started_at, started_clock, observations)
        except Exception as exc:
            return self._complete_run(self.aviation, started_at, started_clock, [], str(exc))

    def run_maritime(self, **kwargs: Any) -> ProviderRun:
        started_at = _now()
        started_clock = time.monotonic()
        if not self.maritime.configured():
            return self._complete_run(
                self.maritime,
                started_at,
                started_clock,
                [],
                f"{self.maritime.env_key} is not configured",
            )
        try:
            observations = self.maritime.observations(self.maritime.fetch(**kwargs))
            return self._complete_run(self.maritime, started_at, started_clock, observations)
        except Exception as exc:
            return self._complete_run(self.maritime, started_at, started_clock, [], str(exc))

    def configuration(self) -> dict[str, Any]:
        return {
            "aviation": {"provider": self.aviation.name, "configured": True, "key_required": False},
            "maritime": {
                "provider": self.maritime.name,
                "configured": self.maritime.configured(),
                "key_required": True,
                "credential_env": self.maritime.env_key,
            },
            "credentials_never_returned": True,
        }
