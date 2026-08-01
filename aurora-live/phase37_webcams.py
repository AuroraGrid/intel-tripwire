from __future__ import annotations

import ipaddress
import json
import socket
import sqlite3
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from phase33_webcams import (
    HEALTH_STATES,
    REGIONS,
    SOURCE_TYPES,
    Webcam,
    _coordinate,
    _now,
    _text,
    _url,
)


def _is_postgres(target: str) -> bool:
    return target.startswith(("postgresql://", "postgres://"))


class WebcamStore:
    """Durable SQLite/PostgreSQL store for curated cameras and health evidence."""

    def __init__(self, target: str = ":memory:") -> None:
        self.target = str(target)
        self.postgres = _is_postgres(self.target)
        self._lock = threading.RLock()
        if self.postgres:
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc:
                raise RuntimeError("psycopg is required for PostgreSQL webcam storage") from exc
            self._connection = psycopg.connect(self.target, row_factory=dict_row)
            self._p = "%s"
        else:
            if self.target != ":memory:":
                Path(self.target).parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(self.target, check_same_thread=False)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._p = "?"
        self._initialize()

    def _initialize(self) -> None:
        observation_pk = (
            "health_observation_id BIGSERIAL PRIMARY KEY"
            if self.postgres
            else "health_observation_id INTEGER PRIMARY KEY AUTOINCREMENT"
        )
        statements = [
            """CREATE TABLE IF NOT EXISTS webcam_sources (
                webcam_id TEXT PRIMARY KEY, region TEXT NOT NULL, country TEXT NOT NULL,
                city TEXT NOT NULL, title TEXT NOT NULL, source_type TEXT NOT NULL,
                source_url TEXT NOT NULL, embed_url TEXT NOT NULL,
                latitude DOUBLE PRECISION NOT NULL, longitude DOUBLE PRECISION NOT NULL,
                provider TEXT NOT NULL, attribution TEXT NOT NULL, license_note TEXT NOT NULL,
                health TEXT NOT NULL, last_checked_at TEXT NOT NULL,
                last_success_at TEXT NOT NULL, consecutive_failures INTEGER NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
            f"""CREATE TABLE IF NOT EXISTS webcam_health_observations (
                {observation_pk}, webcam_id TEXT NOT NULL REFERENCES webcam_sources(webcam_id),
                observed_health TEXT NOT NULL, effective_health TEXT NOT NULL,
                checked_at TEXT NOT NULL, detail_json TEXT NOT NULL, created_at TEXT NOT NULL)""",
            "CREATE INDEX IF NOT EXISTS idx_webcams_region_health ON webcam_sources(region, health)",
            "CREATE INDEX IF NOT EXISTS idx_webcam_health_time ON webcam_health_observations(webcam_id, health_observation_id DESC)",
        ]
        with self._lock:
            cursor = self._connection.cursor()
            for statement in statements:
                cursor.execute(statement)
            self._connection.commit()

    @staticmethod
    def _dict(row: Any) -> dict[str, Any]:
        return dict(row) if row is not None else {}

    def upsert_source(self, value: dict[str, Any]) -> None:
        columns = (
            "webcam_id",
            "region",
            "country",
            "city",
            "title",
            "source_type",
            "source_url",
            "embed_url",
            "latitude",
            "longitude",
            "provider",
            "attribution",
            "license_note",
            "health",
            "last_checked_at",
            "last_success_at",
            "consecutive_failures",
            "created_at",
            "updated_at",
        )
        p = self._p
        updates = ",".join(f"{column}=excluded.{column}" for column in columns[1:] if column != "created_at")
        sql = (
            f"INSERT INTO webcam_sources({','.join(columns)}) "
            f"VALUES ({','.join([p] * len(columns))}) "
            f"ON CONFLICT(webcam_id) DO UPDATE SET {updates}"
        )
        with self._lock:
            self._connection.execute(sql, tuple(value.get(column, "") for column in columns))
            self._connection.commit()

    def source(self, webcam_id: str) -> dict[str, Any]:
        p = self._p
        with self._lock:
            row = self._connection.execute(
                f"SELECT * FROM webcam_sources WHERE webcam_id={p}", (webcam_id,)
            ).fetchone()
        return self._dict(row)

    def sources(self, region: str = "", health: str = "", limit: int = 1000) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 5000))
        filters: list[str] = []
        values: list[Any] = []
        if region:
            filters.append(f"region={self._p}")
            values.append(region)
        if health:
            filters.append(f"health={self._p}")
            values.append(health)
        where = f" WHERE {' AND '.join(filters)}" if filters else ""
        values.append(limit)
        with self._lock:
            rows = self._connection.execute(
                f"SELECT * FROM webcam_sources{where} ORDER BY region, country, city, title, webcam_id LIMIT {self._p}",
                tuple(values),
            ).fetchall()
        return [self._dict(row) for row in rows]

    def record_health(
        self,
        webcam_id: str,
        observed_health: str,
        effective_health: str,
        checked_at: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        p = self._p
        values = (
            webcam_id,
            observed_health,
            effective_health,
            checked_at,
            json.dumps(detail or {}, sort_keys=True, separators=(",", ":")),
            _now(),
        )
        with self._lock:
            self._connection.execute(
                f"INSERT INTO webcam_health_observations(webcam_id, observed_health, effective_health, checked_at, detail_json, created_at) VALUES ({','.join([p] * 6)})",
                values,
            )
            self._connection.commit()

    def health_history(self, webcam_id: str, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 1000))
        p = self._p
        with self._lock:
            rows = self._connection.execute(
                f"SELECT * FROM webcam_health_observations WHERE webcam_id={p} ORDER BY health_observation_id DESC LIMIT {p}",
                (webcam_id, limit),
            ).fetchall()
        values = [self._dict(row) for row in rows]
        for value in values:
            try:
                value["detail"] = json.loads(value.pop("detail_json", "{}"))
            except json.JSONDecodeError:
                value["detail"] = {}
        return values

    def close(self) -> None:
        """Close the underlying database connection to release file handles (Windows-safe)."""
        try:
            with self._lock:
                conn = getattr(self, "_connection", None)
                if conn is None:
                    return
                try:
                    conn.close()
                except Exception:
                    # best-effort close; ignore errors
                    pass
                finally:
                    self._connection = None
        except Exception:
            # swallow during interpreter teardown
            pass

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


class DurableWebcamRegistry:
    """Phase 33-compatible webcam registry backed by append-only health evidence."""

    def __init__(self, store: WebcamStore) -> None:
        self.store = store

    @staticmethod
    def _id(region: str, source_url: str) -> str:
        import hashlib

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
        previous = self.store.source(webcam_id)
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
            health=previous.get("health", "UNKNOWN"),
            last_checked_at=previous.get("last_checked_at", ""),
            last_success_at=previous.get("last_success_at", ""),
            consecutive_failures=int(previous.get("consecutive_failures", 0)),
            created_at=previous.get("created_at", now),
            updated_at=now,
        )
        self.store.upsert_source(item.value())
        return item.value()

    def observe(self, webcam_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        observed = _text(payload.get("health"), "health", 30).upper()
        if observed not in HEALTH_STATES - {"UNKNOWN"}:
            raise ValueError("health must be ONLINE, DEGRADED, or OFFLINE")
        checked_at = _text(payload.get("checked_at") or _now(), "checked_at", 80)
        previous = self.store.source(webcam_id)
        if not previous:
            raise KeyError("webcam not found")
        failures = 0 if observed == "ONLINE" else int(previous.get("consecutive_failures", 0)) + 1
        effective = "OFFLINE" if observed == "DEGRADED" and failures >= 3 else observed
        item = Webcam(
            **{
                **previous,
                "health": effective,
                "last_checked_at": checked_at,
                "last_success_at": checked_at if observed == "ONLINE" else previous.get("last_success_at", ""),
                "consecutive_failures": failures,
                "updated_at": _now(),
            }
        )
        self.store.upsert_source(item.value())
        detail = payload.get("detail") if isinstance(payload.get("detail"), dict) else {}
        self.store.record_health(webcam_id, observed, effective, checked_at, detail)
        return item.value()

    def get(self, webcam_id: str) -> dict[str, Any]:
        item = self.store.source(webcam_id)
        if not item:
            raise KeyError("webcam not found")
        return item

    def list(self, region: str = "", health: str = "", limit: int = 250) -> list[dict[str, Any]]:
        normalized_region = str(region or "").strip()
        normalized_health = str(health or "").strip().upper()
        if normalized_region and normalized_region not in REGIONS:
            raise ValueError("invalid region")
        if normalized_health and normalized_health not in HEALTH_STATES:
            raise ValueError("invalid health")
        return self.store.sources(normalized_region, normalized_health, limit)

    def coverage(self) -> dict[str, Any]:
        rows = []
        all_items = self.store.sources(limit=5000)
        for region in REGIONS:
            cameras = [item for item in all_items if item["region"] == region]
            online = sum(item["health"] == "ONLINE" for item in cameras)
            degraded = sum(item["health"] == "DEGRADED" for item in cameras)
            offline = sum(item["health"] == "OFFLINE" for item in cameras)
            unknown = sum(item["health"] == "UNKNOWN" for item in cameras)
            rows.append(
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
            "total_required": 70,
            "total_registered": len(all_items),
            "total_online": sum(row["online"] for row in rows),
            "total_gap": sum(row["gap"] for row in rows),
            "regions": rows,
            "qualified_regions": sum(row["qualified"] for row in rows),
            "fully_qualified": all(row["qualified"] for row in rows),
            "generated_at": _now(),
        }

    def matrix(self) -> dict[str, Any]:
        health_rank = {"ONLINE": 0, "DEGRADED": 1, "UNKNOWN": 2, "OFFLINE": 3}
        regions = []
        for coverage in self.coverage()["regions"]:
            cameras = self.list(coverage["region"], limit=1000)
            cameras.sort(
                key=lambda item: (
                    health_rank.get(item["health"], 9),
                    item["country"],
                    item["city"],
                    item["title"],
                    item["webcam_id"],
                )
            )
            slots = []
            for index in range(10):
                camera = cameras[index] if index < len(cameras) else None
                slots.append(
                    {
                        "slot": index + 1,
                        "assigned": camera is not None,
                        "qualified": bool(camera and camera["health"] == "ONLINE"),
                        "webcam": camera,
                    }
                )
            regions.append(
                {
                    **coverage,
                    "slots": slots,
                    "overflow": cameras[10:],
                }
            )
        return {
            "matrix": "seven regions x ten required online cameras",
            "target_slots": 70,
            "regions": regions,
            "assigned_slots": sum(row["registered"] if row["registered"] < 10 else 10 for row in regions),
            "qualified_slots": sum(row["online"] if row["online"] < 10 else 10 for row in regions),
            "fully_qualified": all(row["qualified"] for row in regions),
            "generated_at": _now(),
        }

    def source_health(self) -> dict[str, Any]:
        items = self.store.sources(limit=5000)
        counts = {state: sum(item["health"] == state for item in items) for state in sorted(HEALTH_STATES)}
        total = len(items)
        state = "ONLINE" if total and counts["ONLINE"] == total else "DEGRADED" if counts["ONLINE"] else "OFFLINE" if total else "NOT_CONFIGURED"
        return {
            "feed": "regional-webcams",
            "state": state,
            "total": total,
            "counts": counts,
            "generated_at": _now(),
        }


@dataclass(frozen=True)
class ProbeResponse:
    status: int
    url: str
    headers: dict[str, str]
    body: bytes


def _assert_public_url(url: str) -> None:
    parsed = urlparse(url)
    hostname = str(parsed.hostname or "").lower()
    if not hostname or hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
        raise ValueError("private or local webcam targets are not allowed")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(hostname, parsed.port or 443, type=socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise ValueError("webcam hostname could not be resolved") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ValueError("private or non-global webcam targets are not allowed")


class WebcamHttpTransport:
    def get(self, url: str, *, max_bytes: int = 1_500_000, timeout: float = 12.0) -> ProbeResponse:
        _assert_public_url(url)
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "AURORA-LIVE/37 webcam-health",
                "Accept": "text/html,application/vnd.apple.mpegurl,application/x-mpegURL,image/jpeg,*/*;q=0.5",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                final_url = response.geturl()
                _assert_public_url(final_url)
                body = response.read(max_bytes + 1)
                if len(body) > max_bytes:
                    raise ValueError("webcam response exceeded byte limit")
                return ProbeResponse(
                    status=int(response.status),
                    url=final_url,
                    headers={key.lower(): value for key, value in response.headers.items()},
                    body=body,
                )
        except urllib.error.HTTPError as exc:
            return ProbeResponse(
                status=int(exc.code),
                url=exc.geturl(),
                headers={key.lower(): value for key, value in exc.headers.items()},
                body=exc.read(max_bytes),
            )


class WebcamHealthCoordinator:
    """Conservative health verifier. Reachability alone never proves a live stream."""

    def __init__(self, registry: DurableWebcamRegistry, transport: WebcamHttpTransport | None = None) -> None:
        self.registry = registry
        self.transport = transport or WebcamHttpTransport()

    @staticmethod
    def classify(item: dict[str, Any], response: ProbeResponse) -> tuple[str, dict[str, Any]]:
        content_type = response.headers.get("content-type", "").lower()
        body = response.body
        text = body.decode("utf-8", errors="ignore")
        detail = {
            "status": response.status,
            "final_url": response.url,
            "content_type": content_type,
            "bytes_examined": len(body),
            "verification": "",
        }
        if response.status < 200 or response.status >= 400:
            detail["verification"] = "http-failure"
            return "OFFLINE", detail
        source_type = item["source_type"]
        if source_type == "hls":
            verified = "#EXTM3U" in text and ("#EXTINF" in text or "#EXT-X-STREAM-INF" in text)
            detail["verification"] = "valid-hls-playlist" if verified else "reachable-without-valid-hls-playlist"
            return ("ONLINE" if verified else "DEGRADED"), detail
        if source_type == "mjpeg":
            verified = "multipart/x-mixed-replace" in content_type or body.startswith(b"\xff\xd8\xff")
            detail["verification"] = "mjpeg-or-jpeg-frame" if verified else "reachable-without-mjpeg-frame"
            return ("ONLINE" if verified else "DEGRADED"), detail
        if source_type == "youtube":
            markers = ('"isLive":true', '"isLiveContent":true', '"is_live":true')
            verified = any(marker in text for marker in markers)
            detail["verification"] = "youtube-live-marker" if verified else "youtube-page-reachable-live-unproven"
            return ("ONLINE" if verified else "DEGRADED"), detail
        stream_content = (
            "application/vnd.apple.mpegurl" in content_type
            or "application/x-mpegurl" in content_type
            or "multipart/x-mixed-replace" in content_type
            or "#EXTM3U" in text
            or body.startswith(b"\xff\xd8\xff")
        )
        detail["verification"] = "direct-stream-evidence" if stream_content else "provider-page-reachable-live-unproven"
        return ("ONLINE" if stream_content else "DEGRADED"), detail

    def check(self, webcam_id: str) -> dict[str, Any]:
        item = self.registry.get(webcam_id)
        checked_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        try:
            response = self.transport.get(item["source_url"])
            health, detail = self.classify(item, response)
        except Exception as exc:
            health = "OFFLINE"
            detail = {"verification": "probe-error", "error": f"{type(exc).__name__}: {exc}"}
        return self.registry.observe(
            webcam_id,
            {"health": health, "checked_at": checked_at, "detail": detail},
        )

    def run(self, *, region: str = "", webcam_id: str = "", limit: int = 250) -> dict[str, Any]:
        if webcam_id:
            items = [self.registry.get(webcam_id)]
        else:
            items = self.registry.list(region=region, limit=limit)
        results = []
        for item in items:
            try:
                results.append({"webcam_id": item["webcam_id"], "result": self.check(item["webcam_id"])})
            except Exception as exc:
                results.append({"webcam_id": item["webcam_id"], "error": f"{type(exc).__name__}: {exc}"})
        counts = {state: 0 for state in sorted(HEALTH_STATES)}
        failed = 0
        for row in results:
            if "result" in row:
                counts[row["result"]["health"]] += 1
            else:
                failed += 1
        return {
            "requested": len(items),
            "completed": len(results) - failed,
            "failed": failed,
            "counts": counts,
            "results": results,
            "generated_at": _now(),
        }
