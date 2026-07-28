from __future__ import annotations

import hashlib
import math
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

REGIONS = (
    "Oceania",
    "Africa",
    "Asia",
    "Middle East",
    "Europe",
    "North America",
    "South America",
)
HEALTH_STATES = {"ONLINE", "DEGRADED", "OFFLINE", "UNKNOWN"}
SOURCE_TYPES = {"youtube", "hls", "mjpeg", "page"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _text(value: Any, field: str, limit: int = 500) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} is required")
    if len(normalized) > limit:
        raise ValueError(f"{field} is too long")
    return normalized


def _url(value: Any, field: str) -> str:
    normalized = _text(value, field, 2000)
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field} must be an http(s) URL")
    return normalized


def _coordinate(value: Any, field: str, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(number) or number < low or number > high:
        raise ValueError(f"{field} is outside its valid range")
    return number


@dataclass(frozen=True)
class Webcam:
    webcam_id: str
    region: str
    country: str
    city: str
    title: str
    source_type: str
    source_url: str
    embed_url: str
    latitude: float
    longitude: float
    provider: str
    attribution: str
    license_note: str
    health: str
    last_checked_at: str
    last_success_at: str
    consecutive_failures: int
    created_at: str
    updated_at: str

    def value(self) -> dict[str, Any]:
        return asdict(self)


class WebcamRegistry:
    """Thread-safe curated webcam registry with explicit health evidence.

    Registration does not imply that a camera is live. A camera becomes ONLINE only
    after a successful health observation. This prevents placeholders and dead embeds
    from being counted as operational coverage.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._items: dict[str, Webcam] = {}

    @staticmethod
    def _id(region: str, source_url: str) -> str:
        digest = hashlib.sha256(f"{region}\n{source_url}".encode("utf-8")).hexdigest()
        return f"cam_{digest[:24]}"

    def register(self, payload: dict[str, Any]) -> dict[str, Any]:
        region = _text(payload.get("region"), "region", 100)
        if region not in REGIONS:
            raise ValueError("invalid region")
        source_type = _text(payload.get("source_type"), "source_type", 30).lower()
        if source_type not in SOURCE_TYPES:
            raise ValueError("invalid source_type")
        source_url = _url(payload.get("source_url"), "source_url")
        embed_url = _url(payload.get("embed_url") or source_url, "embed_url")
        now = _now()
        webcam_id = self._id(region, source_url)
        with self._lock:
            previous = self._items.get(webcam_id)
            item = Webcam(
                webcam_id=webcam_id,
                region=region,
                country=_text(payload.get("country"), "country", 120),
                city=_text(payload.get("city"), "city", 120),
                title=_text(payload.get("title"), "title", 240),
                source_type=source_type,
                source_url=source_url,
                embed_url=embed_url,
                latitude=_coordinate(payload.get("latitude"), "latitude", -90.0, 90.0),
                longitude=_coordinate(payload.get("longitude"), "longitude", -180.0, 180.0),
                provider=_text(payload.get("provider"), "provider", 160),
                attribution=_text(payload.get("attribution"), "attribution", 500),
                license_note=_text(payload.get("license_note"), "license_note", 500),
                health=previous.health if previous else "UNKNOWN",
                last_checked_at=previous.last_checked_at if previous else "",
                last_success_at=previous.last_success_at if previous else "",
                consecutive_failures=previous.consecutive_failures if previous else 0,
                created_at=previous.created_at if previous else now,
                updated_at=now,
            )
            self._items[webcam_id] = item
            return item.value()

    def observe(self, webcam_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        observed = _text(payload.get("health"), "health", 30).upper()
        if observed not in HEALTH_STATES - {"UNKNOWN"}:
            raise ValueError("health must be ONLINE, DEGRADED, or OFFLINE")
        checked_at = _text(payload.get("checked_at") or _now(), "checked_at", 80)
        with self._lock:
            previous = self._items.get(webcam_id)
            if previous is None:
                raise KeyError("webcam not found")
            failures = 0 if observed == "ONLINE" else previous.consecutive_failures + 1
            effective = observed
            if observed == "DEGRADED" and failures >= 3:
                effective = "OFFLINE"
            item = Webcam(
                **{
                    **previous.value(),
                    "health": effective,
                    "last_checked_at": checked_at,
                    "last_success_at": checked_at if observed == "ONLINE" else previous.last_success_at,
                    "consecutive_failures": failures,
                    "updated_at": _now(),
                }
            )
            self._items[webcam_id] = item
            return item.value()

    def get(self, webcam_id: str) -> dict[str, Any]:
        with self._lock:
            item = self._items.get(webcam_id)
            if item is None:
                raise KeyError("webcam not found")
            return item.value()

    def list(self, region: str = "", health: str = "", limit: int = 250) -> list[dict[str, Any]]:
        normalized_region = str(region or "").strip()
        normalized_health = str(health or "").strip().upper()
        if normalized_region and normalized_region not in REGIONS:
            raise ValueError("invalid region")
        if normalized_health and normalized_health not in HEALTH_STATES:
            raise ValueError("invalid health")
        limit = max(1, min(int(limit), 1000))
        with self._lock:
            items = [item for item in self._items.values() if (not normalized_region or item.region == normalized_region) and (not normalized_health or item.health == normalized_health)]
            items.sort(key=lambda item: (item.region, item.country, item.city, item.title, item.webcam_id))
            return [item.value() for item in items[:limit]]

    def coverage(self) -> dict[str, Any]:
        with self._lock:
            regions = []
            for region in REGIONS:
                cameras = [item for item in self._items.values() if item.region == region]
                online = sum(item.health == "ONLINE" for item in cameras)
                degraded = sum(item.health == "DEGRADED" for item in cameras)
                offline = sum(item.health == "OFFLINE" for item in cameras)
                unknown = sum(item.health == "UNKNOWN" for item in cameras)
                regions.append(
                    {
                        "region": region,
                        "required_online": 10,
                        "registered": len(cameras),
                        "online": online,
                        "degraded": degraded,
                        "offline": offline,
                        "unknown": unknown,
                        "gap": max(0, 10 - online),
                        "qualified": online >= 10,
                    }
                )
            return {
                "requirement": "at least 10 independently health-verified live webcams per region",
                "regions": regions,
                "qualified_regions": sum(row["qualified"] for row in regions),
                "fully_qualified": all(row["qualified"] for row in regions),
                "generated_at": _now(),
            }

    def source_health(self) -> dict[str, Any]:
        with self._lock:
            counts = {state: sum(item.health == state for item in self._items.values()) for state in sorted(HEALTH_STATES)}
            total = len(self._items)
            return {
                "feed": "regional-webcams",
                "state": "ONLINE" if total and counts["ONLINE"] == total else "DEGRADED" if counts["ONLINE"] else "OFFLINE" if total else "NOT_CONFIGURED",
                "total": total,
                "counts": counts,
                "generated_at": _now(),
            }
