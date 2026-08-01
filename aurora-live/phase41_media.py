from __future__ import annotations

import hashlib
import json
import sqlite3
import struct
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VERIFICATION_STATES = {"UNVERIFIED", "HASHED", "DUPLICATE_OF", "FAILED", "REJECTED"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _is_postgres(target: str) -> bool:
    return target.startswith(("postgresql://", "postgres://"))


def average_hash(data: bytes, size: int = 8) -> str:
    """Simple average hash for JPEG/PNG-ish byte buffers. Not a forensic authenticity claim."""
    if not data:
        return "0" * size
    # Sample evenly across the buffer into size*size cells.
    cells = size * size
    step = max(1, len(data) // cells)
    samples = [data[index * step % len(data)] for index in range(cells)]
    mean = sum(samples) / len(samples)
    bits = "".join("1" if sample >= mean else "0" for sample in samples)
    # Compact to hex
    value = int(bits, 2)
    width = (cells + 3) // 4
    return f"{value:0{width}x}"


def hamming_distance(left: str, right: str) -> int:
    if len(left) != len(right):
        return max(len(left), len(right)) * 4
    distance = 0
    for a, b in zip(left, right):
        distance += bin(int(a, 16) ^ int(b, 16)).count("1")
    return distance


def detect_jpeg_exif(data: bytes) -> bool:
    return data.startswith(b"\xff\xd8\xff") and b"Exif" in data[:65536]


@dataclass(frozen=True)
class MediaAsset:
    media_id: str
    source_url: str
    content_type: str
    byte_size: int
    content_sha256: str
    perceptual_hash: str
    verification_state: str
    license_note: str
    parent_event_id: str
    captured_at: str
    detail: dict[str, Any]

    def value(self) -> dict[str, Any]:
        return asdict(self)


class MediaStore:
    def __init__(self, target: str = ":memory:") -> None:
        self.target = str(target)
        self.postgres = _is_postgres(self.target)
        self._lock = threading.RLock()
        if self.postgres:
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc:
                raise RuntimeError("psycopg is required for PostgreSQL media storage") from exc
            self._connection = psycopg.connect(self.target, row_factory=dict_row)
            self._p = "%s"
        else:
            if self.target != ":memory:":
                Path(self.target).parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(self.target, check_same_thread=False)
            self._connection.row_factory = sqlite3.Row
            self._p = "?"
        self._initialize()

    def _initialize(self) -> None:
        with self._lock:
            self._connection.execute(
                """CREATE TABLE IF NOT EXISTS media_assets (
                    media_id TEXT PRIMARY KEY,
                    source_url TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    byte_size INTEGER NOT NULL,
                    content_sha256 TEXT NOT NULL DEFAULT '',
                    perceptual_hash TEXT NOT NULL DEFAULT '',
                    verification_state TEXT NOT NULL DEFAULT '',
                    license_note TEXT NOT NULL DEFAULT '',
                    parent_event_id TEXT NOT NULL DEFAULT '',
                    captured_at TEXT NOT NULL DEFAULT '',
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT ''
                )"""
            )
            # Fresh Postgres may create media_assets from an older partial definition
            # (CREATE TABLE IF NOT EXISTS will not add missing columns).
            required = {
                "source_url": "TEXT NOT NULL DEFAULT ''",
                "content_type": "TEXT NOT NULL DEFAULT ''",
                "byte_size": "INTEGER NOT NULL DEFAULT 0",
                "content_sha256": "TEXT NOT NULL DEFAULT ''",
                "perceptual_hash": "TEXT NOT NULL DEFAULT ''",
                "verification_state": "TEXT NOT NULL DEFAULT ''",
                "license_note": "TEXT NOT NULL DEFAULT ''",
                "parent_event_id": "TEXT NOT NULL DEFAULT ''",
                "captured_at": "TEXT NOT NULL DEFAULT ''",
                "detail_json": "TEXT NOT NULL DEFAULT '{}'",
                "created_at": "TEXT NOT NULL DEFAULT ''",
            }
            existing = self._media_columns()
            for name, decl in required.items():
                if name not in existing:
                    self._connection.execute(f"ALTER TABLE media_assets ADD COLUMN {name} {decl}")
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_media_hash ON media_assets(content_sha256, perceptual_hash)"
            )
            self._connection.commit()

    def _media_columns(self) -> set[str]:
        if self.postgres:
            rows = self._connection.execute(
                """SELECT column_name FROM information_schema.columns
                   WHERE table_name = 'media_assets'"""
            ).fetchall()
            names: set[str] = set()
            for row in rows:
                if isinstance(row, dict):
                    names.add(str(row.get("column_name") or row.get("COLUMN_NAME") or ""))
                else:
                    names.add(str(row[0]))
            return {n for n in names if n}
        rows = self._connection.execute("PRAGMA table_info(media_assets)").fetchall()
        names = set()
        for row in rows:
            if isinstance(row, dict):
                names.add(str(row.get("name") or ""))
            else:
                # sqlite3.Row: cid, name, type, notnull, dflt_value, pk
                names.add(str(row[1]))
        return {n for n in names if n}

    @staticmethod
    def _dict(row: Any) -> dict[str, Any]:
        return dict(row) if row is not None else {}

    def upsert(self, asset: MediaAsset) -> dict[str, Any]:
        if asset.verification_state not in VERIFICATION_STATES:
            raise ValueError("invalid verification state")
        values = (
            asset.media_id,
            asset.source_url,
            asset.content_type,
            int(asset.byte_size),
            asset.content_sha256,
            asset.perceptual_hash,
            asset.verification_state,
            asset.license_note,
            asset.parent_event_id,
            asset.captured_at or _now(),
            json.dumps(asset.detail, sort_keys=True, separators=(",", ":")),
            _now(),
        )
        columns = (
            "media_id",
            "source_url",
            "content_type",
            "byte_size",
            "content_sha256",
            "perceptual_hash",
            "verification_state",
            "license_note",
            "parent_event_id",
            "captured_at",
            "detail_json",
            "created_at",
        )
        updates = ",".join(f"{column}=excluded.{column}" for column in columns[1:] if column != "created_at")
        sql = (
            f"INSERT INTO media_assets({','.join(columns)}) VALUES ({','.join([self._p]*len(columns))}) "
            f"ON CONFLICT(media_id) DO UPDATE SET {updates}"
        )
        with self._lock:
            self._connection.execute(sql, values)
            self._connection.commit()
        return asset.value()

    def list(self, limit: int = 250, state: str = "") -> list[dict[str, Any]]:
        clauses = []
        values: list[Any] = []
        if state:
            if state not in VERIFICATION_STATES:
                raise ValueError("invalid verification state")
            clauses.append(f"verification_state={self._p}")
            values.append(state)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(max(1, min(int(limit), 2000)))
        with self._lock:
            rows = self._connection.execute(
                f"SELECT * FROM media_assets{where} ORDER BY created_at DESC LIMIT {self._p}",
                tuple(values),
            ).fetchall()
        output = []
        for row in rows:
            item = self._dict(row)
            try:
                item["detail"] = json.loads(item.pop("detail_json", "{}"))
            except json.JSONDecodeError:
                item["detail"] = {}
            output.append(item)
        return output

    def find_by_sha(self, digest: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                f"SELECT * FROM media_assets WHERE content_sha256={self._p} LIMIT 1",
                (digest,),
            ).fetchone()
        if row is None:
            return None
        item = self._dict(row)
        try:
            item["detail"] = json.loads(item.pop("detail_json", "{}"))
        except json.JSONDecodeError:
            item["detail"] = {}
        return item

    def find_near_phash(self, phash: str, *, max_distance: int = 8) -> dict[str, Any] | None:
        for item in self.list(limit=1000):
            if item.get("perceptual_hash") and hamming_distance(phash, item["perceptual_hash"]) <= max_distance:
                return item
        return None


class MediaVerifier:
    """Deterministic media intake. Reachability alone never implies authenticity."""

    MAX_BYTES = 2_000_000

    def __init__(self, store: MediaStore) -> None:
        self.store = store

    def verify_bytes(
        self,
        data: bytes,
        *,
        source_url: str,
        content_type: str = "application/octet-stream",
        license_note: str = "unspecified",
        parent_event_id: str = "",
        captured_at: str = "",
    ) -> dict[str, Any]:
        if len(data) > self.MAX_BYTES:
            media_id = hashlib.sha256(source_url.encode("utf-8")).hexdigest()[:24]
            asset = MediaAsset(
                media_id=f"media_{media_id}",
                source_url=source_url,
                content_type=content_type,
                byte_size=len(data),
                content_sha256="",
                perceptual_hash="",
                verification_state="REJECTED",
                license_note=license_note,
                parent_event_id=parent_event_id,
                captured_at=captured_at or _now(),
                detail={"reason": "byte-limit-exceeded", "authenticity_claim": False},
            )
            return self.store.upsert(asset)

        digest = hashlib.sha256(data).hexdigest()
        existing = self.store.find_by_sha(digest)
        phash = average_hash(data)
        detail = {
            "sha256": digest,
            "perceptual_hash": phash,
            "exif_present": detect_jpeg_exif(data),
            "authenticity_claim": False,
            "verification": "content-hash-and-average-hash-only",
        }
        if existing:
            state = "DUPLICATE_OF"
            detail["duplicate_of"] = existing["media_id"]
        else:
            near = self.store.find_near_phash(phash)
            if near and near.get("content_sha256") != digest:
                state = "DUPLICATE_OF"
                detail["near_duplicate_of"] = near["media_id"]
                detail["hamming_distance"] = hamming_distance(phash, near.get("perceptual_hash", ""))
            else:
                state = "HASHED"
        media_id = f"media_{digest[:24]}"
        asset = MediaAsset(
            media_id=media_id,
            source_url=source_url,
            content_type=content_type,
            byte_size=len(data),
            content_sha256=digest,
            perceptual_hash=phash,
            verification_state=state,
            license_note=license_note,
            parent_event_id=parent_event_id,
            captured_at=captured_at or _now(),
            detail=detail,
        )
        return self.store.upsert(asset)

    def coverage(self) -> dict[str, Any]:
        rows = self.store.list(limit=5000)
        counts = {state: sum(1 for row in rows if row.get("verification_state") == state) for state in sorted(VERIFICATION_STATES)}
        return {
            "total": len(rows),
            "counts": counts,
            "authenticity_never_claimed_from_hash_alone": True,
            "generated_at": _now(),
        }
