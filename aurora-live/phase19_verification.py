from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import re
from datetime import datetime, timezone
from typing import Any

from phase15_mesh import stable_id

try:
    from PIL import ExifTags, Image
except ImportError:  # Optional outside the release image.
    ExifTags = None
    Image = None


MEDIA_TYPES = {
    "IMAGE", "VIDEO", "AUDIO", "DOCUMENT", "SCREENSHOT",
    "SOCIAL_POST", "SATELLITE", "OTHER",
}
CHECK_TYPES = {
    "CRYPTOGRAPHIC_HASH", "FILE_SIGNATURE", "METADATA", "PERCEPTUAL_HASH",
    "OCR", "KEYFRAME", "DOCUMENT_SIGNATURE", "SYNTHETIC_MEDIA_RISK",
    "GEOLOCATION", "CHRONOLOCATION", "WEATHER_CONSISTENCY",
    "SHADOW_CONSISTENCY", "LOGO_INSIGNIA", "TRANSCRIPTION",
    "SOURCE_ARCHIVE", "CONTEXT",
}
RESULT_KINDS = {"OBSERVATION", "INFERENCE"}
PRODUCERS = {"DETERMINISTIC", "LOCAL_MODEL", "GROQ", "ANALYST"}
REVIEW_STATES = {
    "UNREVIEWED", "CONSISTENT_WITH_EVIDENCE", "INCONSISTENT_WITH_EVIDENCE",
    "MISLEADING_CONTEXT", "UNRESOLVED",
}
DERIVATIVE_KINDS = {
    "THUMBNAIL", "KEYFRAME", "AUDIO_TRACK", "OCR_TEXT",
    "TRANSCRIPT", "DOCUMENT_PAGE", "ARCHIVE_COPY",
}
HEX_64 = re.compile(r"^[0-9a-f]{64}$")


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def loads(value: Any, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except (TypeError, json.JSONDecodeError):
        return default


def _magic_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"PK\x03\x04"):
        return "application/zip"
    if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return "audio/wav"
    if data.startswith(b"ID3") or data[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"}:
        return "audio/mpeg"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "video/mp4"
    return "application/octet-stream"


class MultimodalVerification:
    """Evidence-preserving media verification.

    Deterministic observations, model inferences, and analyst judgments remain
    separate. No automated check is permitted to label an asset authentic.
    """

    INLINE_LIMIT = 4 * 1024 * 1024

    def __init__(self, store):
        self.store = store
        self._init_schema()

    def _init_schema(self) -> None:
        with self.store.db() as connection:
            connection.executescript("""
            CREATE TABLE IF NOT EXISTS media_assets(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, media_type TEXT NOT NULL,
                mime_type TEXT NOT NULL, sha256 TEXT NOT NULL, perceptual_hash TEXT,
                source_url TEXT, object_uri TEXT, original_filename TEXT,
                size_bytes INTEGER NOT NULL, acquired_at TEXT NOT NULL, captured_at TEXT,
                classification TEXT NOT NULL, metadata TEXT NOT NULL, status TEXT NOT NULL,
                review_state TEXT NOT NULL, created_by TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(workspace_id,sha256)
            );
            CREATE TABLE IF NOT EXISTS media_checks(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, asset_id TEXT NOT NULL,
                check_type TEXT NOT NULL, result_kind TEXT NOT NULL, producer TEXT NOT NULL,
                model TEXT NOT NULL, model_version TEXT NOT NULL, state TEXT NOT NULL,
                score REAL, observation TEXT NOT NULL, inference TEXT NOT NULL,
                uncertainty TEXT NOT NULL, falsifiers TEXT NOT NULL, input_hash TEXT NOT NULL,
                output TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL,
                UNIQUE(workspace_id,asset_id,check_type,producer,model,input_hash)
            );
            CREATE TABLE IF NOT EXISTS media_derivatives(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, asset_id TEXT NOT NULL,
                derivative_kind TEXT NOT NULL, sequence INTEGER NOT NULL, sha256 TEXT NOT NULL,
                object_uri TEXT, mime_type TEXT NOT NULL, metadata TEXT NOT NULL,
                created_by TEXT NOT NULL, created_at TEXT NOT NULL,
                UNIQUE(workspace_id,asset_id,derivative_kind,sequence,sha256)
            );
            CREATE TABLE IF NOT EXISTS media_links(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, asset_id TEXT NOT NULL,
                resource_type TEXT NOT NULL, resource_id TEXT NOT NULL,
                relation_type TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL,
                UNIQUE(workspace_id,asset_id,resource_type,resource_id,relation_type)
            );
            CREATE TABLE IF NOT EXISTS media_reviews(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, asset_id TEXT NOT NULL,
                review_state TEXT NOT NULL, rationale TEXT NOT NULL,
                created_by TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS media_revisions(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, asset_id TEXT NOT NULL,
                revision_number INTEGER NOT NULL, action TEXT NOT NULL,
                before_state TEXT NOT NULL, after_state TEXT NOT NULL,
                reason TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL,
                UNIQUE(workspace_id,asset_id,revision_number)
            );
            CREATE TABLE IF NOT EXISTS media_ai_usage(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, provider TEXT NOT NULL,
                usage_day TEXT NOT NULL, requests INTEGER NOT NULL, failures INTEGER NOT NULL,
                updated_at TEXT NOT NULL, UNIQUE(workspace_id,provider,usage_day)
            );
            CREATE INDEX IF NOT EXISTS idx_media_assets_queue
                ON media_assets(workspace_id,status,review_state,updated_at);
            CREATE INDEX IF NOT EXISTS idx_media_checks_asset
                ON media_checks(workspace_id,asset_id,created_at);
            CREATE INDEX IF NOT EXISTS idx_media_derivatives_asset
                ON media_derivatives(workspace_id,asset_id,derivative_kind,sequence);
            CREATE INDEX IF NOT EXISTS idx_media_links_resource
                ON media_links(workspace_id,resource_type,resource_id);
            CREATE INDEX IF NOT EXISTS idx_media_revisions_asset
                ON media_revisions(workspace_id,asset_id,revision_number);
            """)

    def _workspace(self, actor: dict[str, Any]) -> str:
        return str(actor["workspace_id"])

    def _actor(self, actor: dict[str, Any]) -> str:
        return str(actor.get("id") or "system")

    def _decode(self, encoded: Any) -> bytes:
        if not encoded:
            return b""
        try:
            data = base64.b64decode(str(encoded), validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("content_base64 is invalid") from exc
        if len(data) > self.INLINE_LIMIT:
            raise ValueError("inline media exceeds 4 MiB; use object_uri")
        return data

    def _digest(self, value: Any) -> str:
        digest = str(value or "").strip().lower()
        if not HEX_64.fullmatch(digest):
            raise ValueError("sha256 must be 64 lowercase hexadecimal characters")
        return digest

    def _revision(
        self,
        actor: dict[str, Any],
        asset_id: str,
        action: str,
        before: Any,
        after: Any,
        reason: str,
    ) -> None:
        stamp = now()
        with self.store.db() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(revision_number),0)+1 FROM media_revisions "
                "WHERE workspace_id=? AND asset_id=?",
                (self._workspace(actor), asset_id),
            ).fetchone()
            number = int(row[0])
            revision_id = stable_id(
                "media-revision", self._workspace(actor), asset_id, str(number)
            )
            connection.execute(
                """INSERT INTO media_revisions(
                id,workspace_id,asset_id,revision_number,action,before_state,
                after_state,reason,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    revision_id, self._workspace(actor), asset_id, number, action,
                    json.dumps(before or {}, sort_keys=True),
                    json.dumps(after or {}, sort_keys=True),
                    str(reason or ""), self._actor(actor), stamp,
                ),
            )

    def _image_facts(self, data: bytes) -> dict[str, Any]:
        if not data or Image is None:
            return {}
        try:
            with Image.open(io.BytesIO(data)) as image:
                facts: dict[str, Any] = {
                    "format": image.format,
                    "width": int(image.width),
                    "height": int(image.height),
                    "mode": image.mode,
                }
                gray = image.convert("L").resize((8, 8))
                pixels = list(gray.getdata())
                average = sum(pixels) / len(pixels)
                facts["average_hash"] = "".join(
                    f"{value:02x}" for value in [
                        sum((1 << (7 - bit)) for bit in range(8)
                            if pixels[row * 8 + bit] >= average)
                        for row in range(8)
                    ]
                )
                exif = {}
                if hasattr(image, "getexif"):
                    for key, value in image.getexif().items():
                        name = ExifTags.TAGS.get(key, str(key)) if ExifTags else str(key)
                        if isinstance(value, (str, int, float)):
                            exif[name] = value
                facts["exif"] = exif
                return facts
        except Exception:
            return {}

    def register_asset(
        self, actor: dict[str, Any], payload: dict[str, Any]
    ) -> dict[str, Any]:
        media_type = str(payload.get("media_type") or "OTHER").upper()
        if media_type not in MEDIA_TYPES:
            raise ValueError("invalid media_type")
        classification = str(payload.get("classification") or "PUBLIC").upper()
        if classification not in {"PUBLIC", "INTERNAL", "RESTRICTED"}:
            raise ValueError("invalid classification")
        metadata = payload.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise ValueError("metadata must be an object")

        data = self._decode(payload.get("content_base64"))
        digest = hashlib.sha256(data).hexdigest() if data else self._digest(payload.get("sha256"))
        detected_mime = _magic_mime(data) if data else ""
        mime_type = str(payload.get("mime_type") or detected_mime or "application/octet-stream")
        size_bytes = len(data) if data else int(payload.get("size_bytes") or 0)
        if size_bytes < 0:
            raise ValueError("size_bytes cannot be negative")

        workspace_id = self._workspace(actor)
        with self.store.db() as connection:
            existing = connection.execute(
                "SELECT id FROM media_assets WHERE workspace_id=? AND sha256=?",
                (workspace_id, digest),
            ).fetchone()
        if existing:
            result = self.asset(actor, existing["id"])
            result["deduplicated"] = True
            return result

        image_facts = self._image_facts(data)
        perceptual_hash = str(image_facts.get("average_hash") or "")
        stamp = now()
        asset_id = stable_id("media-asset", workspace_id, digest)
        acquired_at = str(payload.get("acquired_at") or stamp)
        with self.store.db() as connection:
            connection.execute(
                """INSERT INTO media_assets(
                id,workspace_id,media_type,mime_type,sha256,perceptual_hash,
                source_url,object_uri,original_filename,size_bytes,acquired_at,
                captured_at,classification,metadata,status,review_state,created_by,
                created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'PENDING',
                'UNREVIEWED',?,?,?)""",
                (
                    asset_id, workspace_id, media_type, mime_type, digest,
                    perceptual_hash or None, payload.get("source_url"),
                    payload.get("object_uri"), payload.get("original_filename"),
                    size_bytes, acquired_at, payload.get("captured_at"), classification,
                    json.dumps(metadata, sort_keys=True), self._actor(actor), stamp, stamp,
                ),
            )

        self.record_check(actor, asset_id, {
            "check_type": "CRYPTOGRAPHIC_HASH",
            "result_kind": "OBSERVATION",
            "producer": "DETERMINISTIC",
            "state": "OBSERVED",
            "observation": f"SHA-256 {digest}",
            "output": {"sha256": digest, "size_bytes": size_bytes},
        })
        signature_state = "OBSERVED" if detected_mime else "UNAVAILABLE"
        mismatch = bool(detected_mime and payload.get("mime_type") and mime_type != detected_mime)
        if mismatch:
            signature_state = "WARNING"
        self.record_check(actor, asset_id, {
            "check_type": "FILE_SIGNATURE",
            "result_kind": "OBSERVATION",
            "producer": "DETERMINISTIC",
            "state": signature_state,
            "observation": (
                f"Detected {detected_mime}" if detected_mime
                else "Binary content was not available for signature inspection"
            ),
            "uncertainty": "Declared MIME differs from file signature" if mismatch else "",
            "output": {"declared_mime": mime_type, "detected_mime": detected_mime},
        })
        if image_facts:
            self.record_check(actor, asset_id, {
                "check_type": "METADATA",
                "result_kind": "OBSERVATION",
                "producer": "DETERMINISTIC",
                "state": "OBSERVED",
                "observation": "Image structure and embedded metadata extracted",
                "output": image_facts,
            })
            self.record_check(actor, asset_id, {
                "check_type": "PERCEPTUAL_HASH",
                "result_kind": "OBSERVATION",
                "producer": "DETERMINISTIC",
                "state": "OBSERVED",
                "observation": f"Average image hash {perceptual_hash}",
                "output": {"algorithm": "average-hash-8x8", "hash": perceptual_hash},
            })
        with self.store.db() as connection:
            connection.execute(
                "UPDATE media_assets SET status='READY_FOR_REVIEW',updated_at=? "
                "WHERE id=? AND workspace_id=?",
                (now(), asset_id, workspace_id),
            )
        item = self.asset(actor, asset_id)
        self._revision(actor, asset_id, "CREATED", {}, item, "Media evidence registered")
        self.store.identity.audit(
            workspace_id, self._actor(actor), "media.asset.registered",
            "media_asset", asset_id,
            metadata={"media_type": media_type, "classification": classification},
        )
        return item

    def record_check(
        self, actor: dict[str, Any], asset_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self.asset(actor, asset_id, include_children=False)
        check_type = str(payload.get("check_type") or "").upper()
        result_kind = str(payload.get("result_kind") or "OBSERVATION").upper()
        producer = str(payload.get("producer") or "DETERMINISTIC").upper()
        if check_type not in CHECK_TYPES:
            raise ValueError("invalid check_type")
        if result_kind not in RESULT_KINDS:
            raise ValueError("invalid result_kind")
        if producer not in PRODUCERS:
            raise ValueError("invalid producer")
        if producer in {"LOCAL_MODEL", "GROQ"} and result_kind != "INFERENCE":
            raise ValueError("model output must be recorded as inference")
        model = str(payload.get("model") or "")
        model_version = str(payload.get("model_version") or "")
        if producer in {"LOCAL_MODEL", "GROQ"} and not model:
            raise ValueError("model is required for model inference")
        score = payload.get("score")
        if score is not None and not 0 <= float(score) <= 1:
            raise ValueError("score must be between 0 and 1")
        observation = str(payload.get("observation") or "")
        inference = str(payload.get("inference") or "")
        if result_kind == "OBSERVATION" and not observation:
            raise ValueError("observation is required")
        if result_kind == "INFERENCE" and not inference:
            raise ValueError("inference is required")
        output = payload.get("output") or {}
        falsifiers = payload.get("falsifiers") or []
        input_hash = hashlib.sha256(json.dumps({
            "observation": observation,
            "inference": inference,
            "output": output,
            "uncertainty": payload.get("uncertainty") or "",
        }, sort_keys=True).encode("utf-8")).hexdigest()
        check_id = stable_id(
            "media-check", self._workspace(actor), asset_id, check_type,
            producer, model, input_hash,
        )
        stamp = now()
        with self.store.db() as connection:
            connection.execute(
                """INSERT INTO media_checks(
                id,workspace_id,asset_id,check_type,result_kind,producer,model,
                model_version,state,score,observation,inference,uncertainty,
                falsifiers,input_hash,output,created_by,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(workspace_id,asset_id,check_type,producer,model,input_hash)
                DO NOTHING""",
                (
                    check_id, self._workspace(actor), asset_id, check_type,
                    result_kind, producer, model, model_version,
                    str(payload.get("state") or "UNRESOLVED").upper(),
                    float(score) if score is not None else None,
                    observation, inference, str(payload.get("uncertainty") or ""),
                    json.dumps(falsifiers, sort_keys=True), input_hash,
                    json.dumps(output, sort_keys=True), self._actor(actor), stamp,
                ),
            )
            row = connection.execute(
                "SELECT * FROM media_checks WHERE id=? AND workspace_id=?",
                (check_id, self._workspace(actor)),
            ).fetchone()
        return self._check(row)

    def add_derivative(
        self, actor: dict[str, Any], asset_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self.asset(actor, asset_id, include_children=False)
        kind = str(payload.get("derivative_kind") or "").upper()
        if kind not in DERIVATIVE_KINDS:
            raise ValueError("invalid derivative_kind")
        data = self._decode(payload.get("content_base64"))
        digest = hashlib.sha256(data).hexdigest() if data else self._digest(payload.get("sha256"))
        sequence = max(0, int(payload.get("sequence") or 0))
        derivative_id = stable_id(
            "media-derivative", self._workspace(actor), asset_id, kind,
            str(sequence), digest,
        )
        with self.store.db() as connection:
            connection.execute(
                """INSERT INTO media_derivatives(
                id,workspace_id,asset_id,derivative_kind,sequence,sha256,
                object_uri,mime_type,metadata,created_by,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(workspace_id,asset_id,derivative_kind,sequence,sha256)
                DO NOTHING""",
                (
                    derivative_id, self._workspace(actor), asset_id, kind, sequence,
                    digest, payload.get("object_uri"),
                    str(payload.get("mime_type") or _magic_mime(data)),
                    json.dumps(payload.get("metadata") or {}, sort_keys=True),
                    self._actor(actor), now(),
                ),
            )
            row = connection.execute(
                "SELECT * FROM media_derivatives WHERE id=? AND workspace_id=?",
                (derivative_id, self._workspace(actor)),
            ).fetchone()
        item = dict(row)
        item["metadata"] = loads(item["metadata"], {})
        return item

    def link(
        self, actor: dict[str, Any], asset_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self.asset(actor, asset_id, include_children=False)
        resource_type = str(payload.get("resource_type") or "").strip()
        resource_id = str(payload.get("resource_id") or "").strip()
        relation_type = str(payload.get("relation_type") or "EVIDENCE").upper()
        if not resource_type or not resource_id:
            raise ValueError("resource_type and resource_id required")
        link_id = stable_id(
            "media-link", self._workspace(actor), asset_id, resource_type,
            resource_id, relation_type,
        )
        with self.store.db() as connection:
            connection.execute(
                """INSERT INTO media_links(
                id,workspace_id,asset_id,resource_type,resource_id,relation_type,
                created_by,created_at) VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(workspace_id,asset_id,resource_type,resource_id,relation_type)
                DO NOTHING""",
                (
                    link_id, self._workspace(actor), asset_id, resource_type,
                    resource_id, relation_type, self._actor(actor), now(),
                ),
            )
            row = connection.execute(
                "SELECT * FROM media_links WHERE id=? AND workspace_id=?",
                (link_id, self._workspace(actor)),
            ).fetchone()
        return dict(row)

    def review(
        self, actor: dict[str, Any], asset_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        before = self.asset(actor, asset_id)
        state = str(payload.get("review_state") or "").upper()
        rationale = str(payload.get("rationale") or "").strip()
        if state not in REVIEW_STATES - {"UNREVIEWED"}:
            raise ValueError("invalid review_state")
        if not rationale:
            raise ValueError("rationale required")
        stamp = now()
        review_id = stable_id(
            "media-review", self._workspace(actor), asset_id, state,
            self._actor(actor), stamp,
        )
        with self.store.db() as connection:
            connection.execute(
                """INSERT INTO media_reviews(
                id,workspace_id,asset_id,review_state,rationale,created_by,created_at)
                VALUES(?,?,?,?,?,?,?)""",
                (
                    review_id, self._workspace(actor), asset_id, state, rationale,
                    self._actor(actor), stamp,
                ),
            )
            connection.execute(
                "UPDATE media_assets SET review_state=?,status='REVIEWED',updated_at=? "
                "WHERE id=? AND workspace_id=?",
                (state, stamp, asset_id, self._workspace(actor)),
            )
        after = self.asset(actor, asset_id)
        self._revision(actor, asset_id, "ANALYST_REVIEW", before, after, rationale)
        self.store.identity.audit(
            self._workspace(actor), self._actor(actor), "media.asset.reviewed",
            "media_asset", asset_id, metadata={"review_state": state},
        )
        return after

    def reserve_ai(
        self, actor: dict[str, Any], provider: str, daily_limit: int
    ) -> dict[str, Any]:
        provider = str(provider or "").upper()
        if provider != "GROQ":
            raise ValueError("unsupported AI provider")
        day = now()[:10]
        workspace_id = self._workspace(actor)
        usage_id = stable_id("media-ai-usage", workspace_id, provider, day)
        with self.store.db() as connection:
            row = connection.execute(
                "SELECT requests,failures FROM media_ai_usage "
                "WHERE workspace_id=? AND provider=? AND usage_day=?",
                (workspace_id, provider, day),
            ).fetchone()
            requests = int(row["requests"]) if row else 0
            if requests >= max(0, int(daily_limit)):
                raise ValueError("workspace AI daily limit reached")
            if row:
                connection.execute(
                    "UPDATE media_ai_usage SET requests=requests+1,updated_at=? "
                    "WHERE workspace_id=? AND provider=? AND usage_day=?",
                    (now(), workspace_id, provider, day),
                )
            else:
                connection.execute(
                    """INSERT INTO media_ai_usage(
                    id,workspace_id,provider,usage_day,requests,failures,updated_at)
                    VALUES(?,?,?,?,1,0,?)""",
                    (usage_id, workspace_id, provider, day, now()),
                )
        return {"provider": provider, "usage_day": day, "requests": requests + 1,
                "daily_limit": int(daily_limit)}

    def record_ai_failure(self, actor: dict[str, Any], provider: str) -> None:
        day = now()[:10]
        with self.store.db() as connection:
            connection.execute(
                "UPDATE media_ai_usage SET failures=failures+1,updated_at=? "
                "WHERE workspace_id=? AND provider=? AND usage_day=?",
                (now(), self._workspace(actor), str(provider).upper(), day),
            )

    def process_pending(
        self, actor: dict[str, Any], limit: int = 100
    ) -> dict[str, Any]:
        workspace_id = self._workspace(actor)
        with self.store.db() as connection:
            rows = connection.execute(
                "SELECT id FROM media_assets WHERE workspace_id=? AND status='PENDING' "
                "ORDER BY created_at LIMIT ?",
                (workspace_id, max(1, min(1000, int(limit)))),
            ).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE media_assets SET status='READY_FOR_REVIEW',updated_at=? "
                    "WHERE id=? AND workspace_id=?",
                    (now(), row["id"], workspace_id),
                )
        return {"processed": len(rows), "ready_for_review": len(rows)}

    def _check(self, row: Any) -> dict[str, Any]:
        item = dict(row)
        item["score"] = float(item["score"]) if item.get("score") is not None else None
        item["falsifiers"] = loads(item["falsifiers"], [])
        item["output"] = loads(item["output"], {})
        return item

    def asset(
        self, actor: dict[str, Any], asset_id: str, include_children: bool = True
    ) -> dict[str, Any]:
        with self.store.db() as connection:
            row = connection.execute(
                "SELECT * FROM media_assets WHERE id=? AND workspace_id=?",
                (asset_id, self._workspace(actor)),
            ).fetchone()
            if not row:
                raise KeyError("media asset not found")
            item = dict(row)
            if include_children:
                checks = connection.execute(
                    "SELECT * FROM media_checks WHERE workspace_id=? AND asset_id=? "
                    "ORDER BY created_at,id",
                    (self._workspace(actor), asset_id),
                ).fetchall()
                derivatives = connection.execute(
                    "SELECT * FROM media_derivatives WHERE workspace_id=? AND asset_id=? "
                    "ORDER BY derivative_kind,sequence",
                    (self._workspace(actor), asset_id),
                ).fetchall()
                links = connection.execute(
                    "SELECT * FROM media_links WHERE workspace_id=? AND asset_id=? "
                    "ORDER BY created_at",
                    (self._workspace(actor), asset_id),
                ).fetchall()
                reviews = connection.execute(
                    "SELECT * FROM media_reviews WHERE workspace_id=? AND asset_id=? "
                    "ORDER BY created_at",
                    (self._workspace(actor), asset_id),
                ).fetchall()
        item["metadata"] = loads(item["metadata"], {})
        if include_children:
            item["checks"] = [self._check(value) for value in checks]
            item["derivatives"] = []
            for value in derivatives:
                derived = dict(value)
                derived["metadata"] = loads(derived["metadata"], {})
                item["derivatives"].append(derived)
            item["links"] = [dict(value) for value in links]
            item["reviews"] = [dict(value) for value in reviews]
        return item

    def list_assets(
        self, actor: dict[str, Any], filters: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        filters = filters or {}
        sql = "SELECT id FROM media_assets WHERE workspace_id=?"
        args: list[Any] = [self._workspace(actor)]
        for field in ("media_type", "status", "review_state", "classification"):
            if filters.get(field):
                sql += f" AND {field}=?"
                args.append(str(filters[field]).upper())
        sql += " ORDER BY updated_at DESC LIMIT ?"
        args.append(max(1, min(500, int(filters.get("limit") or 100))))
        with self.store.db() as connection:
            rows = connection.execute(sql, args).fetchall()
        return [self.asset(actor, row["id"], include_children=False) for row in rows]

    def revisions(
        self, actor: dict[str, Any], asset_id: str
    ) -> list[dict[str, Any]]:
        self.asset(actor, asset_id, include_children=False)
        with self.store.db() as connection:
            rows = connection.execute(
                "SELECT * FROM media_revisions WHERE workspace_id=? AND asset_id=? "
                "ORDER BY revision_number",
                (self._workspace(actor), asset_id),
            ).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["before_state"] = loads(item["before_state"], {})
            item["after_state"] = loads(item["after_state"], {})
            output.append(item)
        return output

    def scorecard(self, actor: dict[str, Any]) -> dict[str, Any]:
        workspace_id = self._workspace(actor)
        with self.store.db() as connection:
            types = connection.execute(
                "SELECT media_type,COUNT(*) total FROM media_assets "
                "WHERE workspace_id=? GROUP BY media_type", (workspace_id,)
            ).fetchall()
            reviews = connection.execute(
                "SELECT review_state,COUNT(*) total FROM media_assets "
                "WHERE workspace_id=? GROUP BY review_state", (workspace_id,)
            ).fetchall()
            results = connection.execute(
                "SELECT result_kind,COUNT(*) total FROM media_checks "
                "WHERE workspace_id=? GROUP BY result_kind", (workspace_id,)
            ).fetchall()
            usage = connection.execute(
                "SELECT provider,usage_day,requests,failures FROM media_ai_usage "
                "WHERE workspace_id=? ORDER BY usage_day DESC", (workspace_id,)
            ).fetchall()
        return {
            "phase": 19,
            "authenticity_rule": "No automated result is an authenticity verdict",
            "assets_by_type": {row["media_type"]: int(row["total"]) for row in types},
            "reviews": {row["review_state"]: int(row["total"]) for row in reviews},
            "checks_by_kind": {row["result_kind"]: int(row["total"]) for row in results},
            "ai_usage": [dict(row) for row in usage],
        }
