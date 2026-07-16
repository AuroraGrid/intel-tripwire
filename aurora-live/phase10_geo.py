from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Iterable


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _coordinates(item: dict[str, Any]) -> tuple[float, float] | None:
    lat = _number(item.get("latitude"))
    lon = _number(item.get("longitude"))
    if lat is None or lon is None or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        payload = item.get("payload")
        if isinstance(payload, dict):
            lat = _number(payload.get("latitude"))
            lon = _number(payload.get("longitude"))
    if lat is None or lon is None or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        return None
    return lat, lon


def _bbox_contains(lat: float, lon: float, bbox: tuple[float, float, float, float] | None) -> bool:
    if bbox is None:
        return True
    west, south, east, north = bbox
    latitude_ok = south <= lat <= north
    longitude_ok = west <= lon <= east if west <= east else lon >= west or lon <= east
    return latitude_ok and longitude_ok


def _bucket(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    size = max(0.25, 24.0 / max(1, 2 ** min(max(zoom, 0), 8)))
    return math.floor((lat + 90.0) / size), math.floor((lon + 180.0) / size)


@dataclass(frozen=True)
class GeoQuery:
    bbox: tuple[float, float, float, float] | None = None
    categories: frozenset[str] = frozenset()
    severities: frozenset[str] = frozenset()
    since: str = ""
    until: str = ""
    zoom: int = 2
    limit: int = 5000


class GeoIndex:
    def __init__(self, incidents: Iterable[dict[str, Any]]):
        self.items: list[dict[str, Any]] = []
        for source in incidents:
            coordinates = _coordinates(source)
            if coordinates is None:
                continue
            item = dict(source)
            item["latitude"], item["longitude"] = coordinates
            self.items.append(item)

    def filter(self, query: GeoQuery) -> list[dict[str, Any]]:
        result = []
        for item in self.items:
            lat, lon = item["latitude"], item["longitude"]
            if not _bbox_contains(lat, lon, query.bbox):
                continue
            if query.categories and str(item.get("category") or "world") not in query.categories:
                continue
            if query.severities and str(item.get("severity") or "low") not in query.severities:
                continue
            published = str(item.get("published_at") or item.get("updated_at") or "")
            if query.since and published and published < query.since:
                continue
            if query.until and published and published > query.until:
                continue
            result.append(item)
            if len(result) >= max(1, query.limit):
                break
        return result

    def clusters(self, query: GeoQuery) -> list[dict[str, Any]]:
        groups: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
        for item in self.filter(query):
            groups[_bucket(item["latitude"], item["longitude"], query.zoom)].append(item)
        clusters = []
        severity_rank = {"critical": 4, "high": 3, "medium": 2, "low": 1}
        for key, rows in groups.items():
            categories = Counter(str(row.get("category") or "world") for row in rows)
            severities = Counter(str(row.get("severity") or "low") for row in rows)
            strongest = max(severities, key=lambda value: severity_rank.get(value, 0))
            clusters.append({
                "id": f"{key[0]}:{key[1]}",
                "latitude": sum(row["latitude"] for row in rows) / len(rows),
                "longitude": sum(row["longitude"] for row in rows) / len(rows),
                "count": len(rows),
                "severity": strongest,
                "categories": dict(categories),
                "incident_ids": [row.get("id") for row in rows[:50]],
            })
        clusters.sort(key=lambda row: (row["count"], severity_rank.get(row["severity"], 0)), reverse=True)
        return clusters

    def heat(self, query: GeoQuery, precision: int = 3) -> list[dict[str, Any]]:
        cells: dict[tuple[float, float], dict[str, Any]] = {}
        factor = max(1, int(precision))
        for item in self.filter(query):
            lat = round(item["latitude"] * factor) / factor
            lon = round(item["longitude"] * factor) / factor
            cell = cells.setdefault((lat, lon), {"latitude": lat, "longitude": lon, "weight": 0, "count": 0})
            cell["count"] += 1
            cell["weight"] += {"critical": 8, "high": 4, "medium": 2, "low": 1}.get(str(item.get("severity") or "low"), 1)
        return sorted(cells.values(), key=lambda row: row["weight"], reverse=True)

    def dossier(self, country: str, incidents: Iterable[dict[str, Any]]) -> dict[str, Any]:
        needle = country.strip().lower()
        rows = [dict(item) for item in incidents if needle and needle in str(item.get("location_name") or item.get("country") or "").lower()]
        severities = Counter(str(item.get("severity") or "low") for item in rows)
        categories = Counter(str(item.get("category") or "world") for item in rows)
        actions = Counter(str(item.get("action_state") or item.get("action") or "MONITOR") for item in rows)
        score = min(100, severities.get("critical", 0) * 18 + severities.get("high", 0) * 8 + severities.get("medium", 0) * 3 + len(rows))
        return {
            "country": country,
            "incident_count": len(rows),
            "risk_score": score,
            "risk_band": "critical" if score >= 80 else "high" if score >= 55 else "elevated" if score >= 30 else "normal",
            "severities": dict(severities),
            "categories": dict(categories),
            "actions": dict(actions),
            "incidents": rows[:100],
        }


def parse_bbox(value: str) -> tuple[float, float, float, float] | None:
    if not value:
        return None
    parts = [float(part.strip()) for part in value.split(",")]
    if len(parts) != 4:
        raise ValueError("bbox must be west,south,east,north")
    west, south, east, north = parts
    if not (-180 <= west <= 180 and -180 <= east <= 180 and -90 <= south <= north <= 90):
        raise ValueError("invalid bbox")
    return west, south, east, north
