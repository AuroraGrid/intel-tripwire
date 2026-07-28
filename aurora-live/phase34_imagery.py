from __future__ import annotations

import hashlib
import math
import re
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from phase33_webcams import REGIONS

IMAGE_STATES = {"FRESH", "STALE", "DEGRADED", "OFFLINE", "UNKNOWN"}
IMAGE_CATEGORIES = (
    "satellite",
    "radar",
    "weather",
    "wildfire",
    "volcano",
    "traffic",
    "disaster",
    "infrastructure",
    "maritime",
    "aviation",
    "government",
    "sensor",
    "other",
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _now() -> str:
    return _iso(_now_dt())


def _text(value: Any, field: str, limit: int = 500) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} is required")
    if len(normalized) > limit:
        raise ValueError(f"{field} is too long")
    return normalized


def _optional_text(value: Any, limit: int = 500) -> str:
    normalized = str(value or "").strip()
    if len(normalized) > limit:
        raise ValueError("value is too long")
    return normalized


def _url(value: Any, field: str) -> str:
    normalized = _text(value, field, 2000)
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field} must be an http(s) URL")
    return normalized


def _coordinate(value: Any, field: str, low: float, high: float) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(number) or number < low or number > high:
        raise ValueError(f"{field} is outside its valid range")
    return number


def _bounded_int(value: Any, field: str, low: int, high: int, default: int | None = None) -> int:
    if value in (None, "") and default is not None:
        return default
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if number < low or number > high:
        raise ValueError(f"{field} is outside its valid range")
    return number


def _instant(value: Any, field: str) -> tuple[datetime, str]:
    normalized = _text(value, field, 80)
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    parsed = parsed.astimezone(timezone.utc)
    return parsed, _iso(parsed)


def _hash(value: Any) -> str:
    normalized = _text(value, "content_sha256", 64).lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise ValueError("content_sha256 must be a 64-character lowercase hexadecimal digest")
    return normalized


@dataclass(frozen=True)
class ImageSource:
    source_id: str
    region: str
    country: str
    title: str
    category: str
    geographic_scope: str
    source_url: str
    image_url: str
    latitude: float | None
    longitude: float | None
    provider: str
    attribution: str
    license_note: str
    refresh_interval_seconds: int
    max_age_seconds: int
    state: str
    last_observed_at: str
    last_captured_at: str
    last_changed_at: str
    content_sha256: str
    content_type: str
    byte_length: int
    width: int
    height: int
    duplicate_of: str
    stale_cycles: int
    consecutive_failures: int
    created_at: str
    updated_at: str

    def value(self) -> dict[str, Any]:
        return asdict(self)


class ImageRegistry:
    """Curated still-image sources with explicit freshness and lineage evidence.

    Registration never means that an image is current. A source becomes FRESH only
    after an observation supplies a valid image digest, capture time, content type,
    byte length, and a freshness result derived from the source's age policy.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._items: dict[str, ImageSource] = {}

    @staticmethod
    def _id(region: str, source_url: str, image_url: str) -> str:
        digest = hashlib.sha256(f"{region}\n{source_url}\n{image_url}".encode("utf-8")).hexdigest()
        return f"img_{digest[:24]}"

    def register(self, payload: dict[str, Any]) -> dict[str, Any]:
        region = _text(payload.get("region"), "region", 100)
        if region not in REGIONS:
            raise ValueError("invalid region")
        category = _text(payload.get("category"), "category", 40).lower()
        if category not in IMAGE_CATEGORIES:
            raise ValueError("invalid category")
        source_url = _url(payload.get("source_url"), "source_url")
        image_url = _url(payload.get("image_url"), "image_url")
        latitude = _coordinate(payload.get("latitude"), "latitude", -90.0, 90.0)
        longitude = _coordinate(payload.get("longitude"), "longitude", -180.0, 180.0)
        if (latitude is None) != (longitude is None):
            raise ValueError("latitude and longitude must be supplied together")
        now = _now()
        source_id = self._id(region, source_url, image_url)
        with self._lock:
            previous = self._items.get(source_id)
            item = ImageSource(
                source_id=source_id,
                region=region,
                country=_optional_text(payload.get("country"), 120),
                title=_text(payload.get("title"), "title", 240),
                category=category,
                geographic_scope=_text(payload.get("geographic_scope"), "geographic_scope", 500),
                source_url=source_url,
                image_url=image_url,
                latitude=latitude,
                longitude=longitude,
                provider=_text(payload.get("provider"), "provider", 160),
                attribution=_text(payload.get("attribution"), "attribution", 500),
                license_note=_text(payload.get("license_note"), "license_note", 500),
                refresh_interval_seconds=_bounded_int(payload.get("refresh_interval_seconds"), "refresh_interval_seconds", 15, 86400, 300),
                max_age_seconds=_bounded_int(payload.get("max_age_seconds"), "max_age_seconds", 30, 604800, 1800),
                state=previous.state if previous else "UNKNOWN",
                last_observed_at=previous.last_observed_at if previous else "",
                last_captured_at=previous.last_captured_at if previous else "",
                last_changed_at=previous.last_changed_at if previous else "",
                content_sha256=previous.content_sha256 if previous else "",
                content_type=previous.content_type if previous else "",
                byte_length=previous.byte_length if previous else 0,
                width=previous.width if previous else 0,
                height=previous.height if previous else 0,
                duplicate_of=previous.duplicate_of if previous else "",
                stale_cycles=previous.stale_cycles if previous else 0,
                consecutive_failures=previous.consecutive_failures if previous else 0,
                created_at=previous.created_at if previous else now,
                updated_at=now,
            )
            self._items[source_id] = item
            return item.value()

    def observe(self, source_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        reported = _text(payload.get("state"), "state", 30).upper()
        if reported not in IMAGE_STATES - {"UNKNOWN"}:
            raise ValueError("state must be FRESH, STALE, DEGRADED, or OFFLINE")
        observed_dt, observed_at = _instant(payload.get("observed_at") or _now(), "observed_at")

        with self._lock:
            previous = self._items.get(source_id)
            if previous is None:
                raise KeyError("image source not found")

            values = previous.value()
            effective = reported
            stale_cycles = previous.stale_cycles
            failures = previous.consecutive_failures
            duplicate_of = previous.duplicate_of

            if reported in {"FRESH", "STALE"}:
                captured_dt, captured_at = _instant(payload.get("captured_at"), "captured_at")
                if captured_dt > observed_dt + timedelta(minutes=5):
                    raise ValueError("captured_at cannot be materially later than observed_at")
                digest = _hash(payload.get("content_sha256"))
                content_type = _text(payload.get("content_type"), "content_type", 120).lower()
                if not content_type.startswith("image/"):
                    raise ValueError("content_type must be an image MIME type")
                byte_length = _bounded_int(payload.get("byte_length"), "byte_length", 1, 2_000_000_000)
                width = _bounded_int(payload.get("width"), "width", 1, 100000)
                height = _bounded_int(payload.get("height"), "height", 1, 100000)

                same_content = bool(previous.content_sha256) and digest == previous.content_sha256
                stale_cycles = previous.stale_cycles + 1 if same_content else 0
                last_changed_at = previous.last_changed_at if same_content and previous.last_changed_at else observed_at
                age_seconds = max(0.0, (observed_dt - captured_dt).total_seconds())
                replay_window = max(previous.max_age_seconds, previous.refresh_interval_seconds * 2)
                replay_seconds = 0.0
                if same_content and last_changed_at:
                    changed_dt, _ = _instant(last_changed_at, "last_changed_at")
                    replay_seconds = max(0.0, (observed_dt - changed_dt).total_seconds())

                if reported == "FRESH" and (age_seconds > previous.max_age_seconds or replay_seconds > replay_window):
                    effective = "STALE"

                duplicates = sorted(
                    item.source_id
                    for item in self._items.values()
                    if item.source_id != source_id and item.content_sha256 == digest
                )
                duplicate_of = duplicates[0] if duplicates else ""
                failures = 0 if effective == "FRESH" else previous.consecutive_failures + 1

                values.update(
                    {
                        "last_captured_at": captured_at,
                        "last_changed_at": last_changed_at,
                        "content_sha256": digest,
                        "content_type": content_type,
                        "byte_length": byte_length,
                        "width": width,
                        "height": height,
                        "duplicate_of": duplicate_of,
                    }
                )
            else:
                failures = previous.consecutive_failures + 1
                if reported == "DEGRADED" and failures >= 3:
                    effective = "OFFLINE"

            values.update(
                {
                    "state": effective,
                    "last_observed_at": observed_at,
                    "stale_cycles": stale_cycles,
                    "consecutive_failures": failures,
                    "updated_at": _now(),
                }
            )
            item = ImageSource(**values)
            self._items[source_id] = item
            return item.value()

    def get(self, source_id: str) -> dict[str, Any]:
        with self._lock:
            item = self._items.get(source_id)
            if item is None:
                raise KeyError("image source not found")
            return item.value()

    def list(self, region: str = "", category: str = "", state: str = "", limit: int = 250) -> list[dict[str, Any]]:
        normalized_region = str(region or "").strip()
        normalized_category = str(category or "").strip().lower()
        normalized_state = str(state or "").strip().upper()
        if normalized_region and normalized_region not in REGIONS:
            raise ValueError("invalid region")
        if normalized_category and normalized_category not in IMAGE_CATEGORIES:
            raise ValueError("invalid category")
        if normalized_state and normalized_state not in IMAGE_STATES:
            raise ValueError("invalid state")
        limit = max(1, min(int(limit), 1000))
        with self._lock:
            items = [
                item
                for item in self._items.values()
                if (not normalized_region or item.region == normalized_region)
                and (not normalized_category or item.category == normalized_category)
                and (not normalized_state or item.state == normalized_state)
            ]
            items.sort(key=lambda item: (item.region, item.category, item.provider, item.title, item.source_id))
            return [item.value() for item in items[:limit]]

    def latest(self, region: str = "", category: str = "", limit: int = 100) -> list[dict[str, Any]]:
        items = self.list(region, category, "FRESH", 1000)
        items.sort(key=lambda item: (item["last_captured_at"], item["source_id"]), reverse=True)
        return items[: max(1, min(int(limit), 250))]

    def coverage(self) -> dict[str, Any]:
        with self._lock:
            rows = []
            matrix = []
            for region in REGIONS:
                regional = [item for item in self._items.values() if item.region == region]
                counts = {state: sum(item.state == state for item in regional) for state in sorted(IMAGE_STATES)}
                rows.append(
                    {
                        "region": region,
                        "registered": len(regional),
                        "fresh": counts["FRESH"],
                        "stale": counts["STALE"],
                        "degraded": counts["DEGRADED"],
                        "offline": counts["OFFLINE"],
                        "unknown": counts["UNKNOWN"],
                        "baseline_qualified": counts["FRESH"] >= 1,
                    }
                )
                matrix.append(
                    {
                        "region": region,
                        "categories": {
                            category: sum(item.category == category and item.state == "FRESH" for item in regional)
                            for category in IMAGE_CATEGORIES
                        },
                    }
                )
            return {
                "requirement": "at least one freshness-verified image source per region for baseline global coverage",
                "regions": rows,
                "category_matrix": matrix,
                "qualified_regions": sum(row["baseline_qualified"] for row in rows),
                "fully_qualified": all(row["baseline_qualified"] for row in rows),
                "generated_at": _now(),
            }

    def source_health(self) -> dict[str, Any]:
        with self._lock:
            counts = {state: sum(item.state == state for item in self._items.values()) for state in sorted(IMAGE_STATES)}
            total = len(self._items)
            if not total:
                state = "NOT_CONFIGURED"
            elif counts["FRESH"] == total:
                state = "ONLINE"
            elif counts["FRESH"] or counts["STALE"] or counts["DEGRADED"]:
                state = "DEGRADED"
            else:
                state = "OFFLINE"
            return {
                "feed": "live-imagery",
                "state": state,
                "total": total,
                "counts": counts,
                "duplicate_sources": sum(bool(item.duplicate_of) for item in self._items.values()),
                "generated_at": _now(),
            }
