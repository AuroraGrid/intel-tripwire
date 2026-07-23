from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from typing import Any

from phase15_mesh import stable_id


STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "have",
    "in", "is", "it", "of", "on", "or", "that", "the", "to", "was", "were", "with",
}
AUTHORITY_TIERS = {"primary": 1, "sensor_network": 2, "market": 2, "secondary": 3}
AUTHORITY_RELIABILITY = {"primary": 0.95, "sensor_network": 0.85, "market": 0.8, "secondary": 0.65}
VALID_REVIEW_STATES = {"CONFIRMED", "DISMISSED", "MONITORING", "ESCALATED"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalize_text(value: Any) -> str:
    text = re.sub(r"https?://\S+", " ", str(value or "").lower())
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return " ".join(text.split())


def tokens(value: Any) -> set[str]:
    return {token for token in normalize_text(value).split() if len(token) > 2 and token not in STOPWORDS}


def jaccard(left: Any, right: Any) -> float:
    a, b = tokens(left), tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0088
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    value = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(value), math.sqrt(max(0.0, 1 - value)))


def loads(value: Any, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except (TypeError, json.JSONDecodeError):
        return default


class DetectionEngine:
    """Deterministic observation correlation with provenance, revisions, and claim integration."""

    def __init__(self, store, mesh, integrity):
        self.store = store
        self.mesh = mesh
        self.integrity = integrity
        self._init_schema()

    def _init_schema(self) -> None:
        with self.store.db() as connection:
            connection.executescript("""
            CREATE TABLE IF NOT EXISTS autonomous_detections(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, canonical_key TEXT NOT NULL,
                title TEXT NOT NULL, domain TEXT NOT NULL, severity TEXT NOT NULL,
                latitude REAL, longitude REAL, state TEXT NOT NULL, review_state TEXT,
                confidence REAL NOT NULL, source_families INTEGER NOT NULL,
                supporting_observations INTEGER NOT NULL, contradicting_observations INTEGER NOT NULL,
                claim_id TEXT, first_observed_at TEXT NOT NULL, last_observed_at TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(workspace_id,canonical_key)
            );
            CREATE TABLE IF NOT EXISTS detection_observations(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, detection_id TEXT NOT NULL,
                observation_id TEXT NOT NULL, relation TEXT NOT NULL, source_family TEXT NOT NULL,
                similarity REAL NOT NULL, rationale TEXT NOT NULL, created_at TEXT NOT NULL,
                UNIQUE(workspace_id,observation_id)
            );
            CREATE TABLE IF NOT EXISTS detection_revisions(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, detection_id TEXT NOT NULL,
                from_state TEXT NOT NULL, to_state TEXT NOT NULL,
                from_confidence REAL NOT NULL, to_confidence REAL NOT NULL,
                reason TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_detections_workspace ON autonomous_detections(workspace_id,domain,updated_at);
            CREATE INDEX IF NOT EXISTS idx_detection_observations_detection ON detection_observations(workspace_id,detection_id,created_at);
            CREATE INDEX IF NOT EXISTS idx_detection_revisions_detection ON detection_revisions(workspace_id,detection_id,created_at);
            """)

    def _workspace(self, actor: dict[str, Any]) -> str:
        return str(actor["workspace_id"])

    def _actor_id(self, actor: dict[str, Any]) -> str:
        return str(actor.get("id") or "system")

    def _observation(self, actor: dict[str, Any], observation_id: str) -> dict[str, Any]:
        with self.store.db() as connection:
            row = connection.execute(
                """SELECT o.*,r.provider,r.authority,r.logical_id
                FROM sensor_observations o JOIN sensor_registry r
                ON r.workspace_id=o.workspace_id AND r.logical_id=o.sensor_id
                WHERE o.id=? AND o.workspace_id=?""",
                (observation_id, self._workspace(actor)),
            ).fetchone()
        if not row:
            raise KeyError("observation not found")
        item = dict(row)
        item["payload"] = loads(item.get("payload"), {})
        item["source_family"] = normalize_text(item.get("provider") or item.get("sensor_id")).replace(" ", "-")
        return item

    def _candidate_rows(self, actor: dict[str, Any], observation: dict[str, Any]) -> list[dict[str, Any]]:
        with self.store.db() as connection:
            rows = connection.execute(
                """SELECT * FROM autonomous_detections
                WHERE workspace_id=? AND domain=? AND state!='CLOSED'
                ORDER BY last_observed_at DESC LIMIT 200""",
                (self._workspace(actor), observation["domain"]),
            ).fetchall()
        return [dict(row) for row in rows]

    def _score(self, observation: dict[str, Any], detection: dict[str, Any]) -> tuple[float, str]:
        text_score = jaccard(observation["title"], detection["title"])
        time_hours = abs((parse_time(observation["observed_at"]) - parse_time(detection["last_observed_at"])).total_seconds()) / 3600
        time_score = 1.0 if time_hours <= 1 else 0.8 if time_hours <= 6 else 0.4 if time_hours <= 24 else 0.0
        geo_score = 0.0
        distance = None
        if observation.get("latitude") is not None and observation.get("longitude") is not None and detection.get("latitude") is not None and detection.get("longitude") is not None:
            distance = haversine_km(float(observation["latitude"]), float(observation["longitude"]), float(detection["latitude"]), float(detection["longitude"]))
            geo_score = 1.0 if distance <= 10 else 0.8 if distance <= 100 else 0.3 if distance <= 500 else 0.0
        external_score = 0.0
        external_id = str(observation.get("external_id") or "").strip()
        if external_id:
            with self.store.db() as connection:
                match = connection.execute(
                    """SELECT 1 FROM detection_observations d JOIN sensor_observations o ON o.id=d.observation_id
                    WHERE d.workspace_id=? AND d.detection_id=? AND o.external_id=? LIMIT 1""",
                    (observation["workspace_id"], detection["id"], external_id),
                ).fetchone()
            external_score = 1.0 if match else 0.0
        score = round(0.55 * text_score + 0.20 * geo_score + 0.15 * time_score + 0.10 * external_score, 4)
        rationale = f"text={text_score:.3f};time={time_score:.3f};geo={geo_score:.3f};external={external_score:.3f}"
        if distance is not None:
            rationale += f";distance_km={distance:.1f}"
        return score, rationale

    def _explicit_relation(self, observation: dict[str, Any], detection_id: str) -> str:
        payload = observation.get("payload") or {}
        target = str(payload.get("contradicts_detection_id") or "").strip()
        relation = str(payload.get("detection_relation") or payload.get("relation") or "").strip().upper()
        if target and target == detection_id:
            return "CONTRADICTS"
        if relation in {"CONTRADICTS", "CORROBORATES", "DUPLICATES"}:
            return relation
        return ""

    def _relation(self, observation: dict[str, Any], detection: dict[str, Any], score: float) -> str:
        explicit = self._explicit_relation(observation, detection["id"])
        if explicit:
            return explicit
        with self.store.db() as connection:
            rows = connection.execute(
                """SELECT d.source_family,o.external_id,o.title FROM detection_observations d
                JOIN sensor_observations o ON o.id=d.observation_id
                WHERE d.workspace_id=? AND d.detection_id=?""",
                (observation["workspace_id"], detection["id"]),
            ).fetchall()
        for row in rows:
            same_external = bool(observation.get("external_id") and row["external_id"] == observation["external_id"])
            same_family = row["source_family"] == observation["source_family"]
            if same_family and (same_external or jaccard(row["title"], observation["title"]) >= 0.92):
                return "DUPLICATES"
        return "CORROBORATES" if score >= 0.58 else "ORIGINATES"

    def _canonical_key(self, observation: dict[str, Any]) -> str:
        # Correlation, not a lossy title/date key, decides whether later observations join.
        return stable_id("detection-key", observation["workspace_id"], observation["id"])

    def _source_payload(self, observation: dict[str, Any]) -> dict[str, Any]:
        authority = str(observation.get("authority") or "secondary").lower()
        payload = observation.get("payload") or {}
        url = str(payload.get("url") or payload.get("source_url") or f"sensor://{observation['sensor_id']}")
        return {
            "name": f"{observation.get('provider') or observation['sensor_id']} [{observation['sensor_id']}]",
            "owner": str(observation.get("provider") or ""),
            "canonical_url": url,
            "lineage_key": observation["source_family"],
            "source_tier": AUTHORITY_TIERS.get(authority, 4),
            "method": str(observation.get("sensor_id") or "sensor"),
            "jurisdiction": str(payload.get("jurisdiction") or ""),
            "reliability": AUTHORITY_RELIABILITY.get(authority, 0.5),
        }

    def _link_claim(self, actor: dict[str, Any], detection_id: str, observation: dict[str, Any], relation: str, similarity: float) -> str:
        with self.store.db() as connection:
            row = connection.execute(
                "SELECT claim_id FROM autonomous_detections WHERE id=? AND workspace_id=?",
                (detection_id, self._workspace(actor)),
            ).fetchone()
        claim_id = str(row["claim_id"] or "") if row else ""
        if not claim_id:
            claim = self.integrity.create_claim(actor, {
                "statement": observation["title"],
                "claim_type": "UNVERIFIED_CLAIM",
                "scope": observation["domain"],
                "falsifier": "A higher-tier independent record or direct sensor observation that disproves the detected event.",
            })
            claim_id = claim["id"]
            with self.store.db() as connection:
                connection.execute(
                    "UPDATE autonomous_detections SET claim_id=? WHERE id=? AND workspace_id=?",
                    (claim_id, detection_id, self._workspace(actor)),
                )
        source = self.integrity.register_source(actor, self._source_payload(observation))
        payload = observation.get("payload") or {}
        evidence_relation = "CONTRADICTS" if relation == "CONTRADICTS" else "DUPLICATES" if relation == "DUPLICATES" else "SUPPORTS"
        self.integrity.add_evidence(actor, claim_id, {
            "source_origin_id": source["id"],
            "title": observation["title"],
            "url": str(payload.get("url") or payload.get("source_url") or ""),
            "published_at": observation["observed_at"],
            "retrieved_at": now(),
            "text": str(payload.get("text") or observation["title"]),
            "relation": evidence_relation,
            "metadata": {"detection_id": detection_id, "observation_id": observation["id"], "similarity": similarity},
        })
        self.integrity.assessment(actor, claim_id, persist=True, reason="Phase 16 autonomous detection reassessment")
        return claim_id

    def _create_detection(self, actor: dict[str, Any], observation: dict[str, Any]) -> dict[str, Any]:
        stamp = now()
        canonical_key = self._canonical_key(observation)
        detection_id = stable_id("detection", self._workspace(actor), canonical_key)
        authority = str(observation.get("authority") or "secondary").lower()
        confidence = round(AUTHORITY_RELIABILITY.get(authority, 0.5) * 0.65, 4)
        with self.store.db() as connection:
            connection.execute(
                """INSERT INTO autonomous_detections(
                id,workspace_id,canonical_key,title,domain,severity,latitude,longitude,state,review_state,
                confidence,source_families,supporting_observations,contradicting_observations,claim_id,
                first_observed_at,last_observed_at,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (detection_id, self._workspace(actor), canonical_key, observation["title"], observation["domain"],
                 str(observation.get("severity") or "unknown"), observation.get("latitude"), observation.get("longitude"),
                 "OPEN", None, confidence, 1, 1, 0, None, observation["observed_at"], observation["observed_at"], stamp, stamp),
            )
        self._attach(actor, detection_id, observation, "ORIGINATES", 1.0, "first observation created detection")
        self.store.identity.audit(self._workspace(actor), self._actor_id(actor), "detection.created", "detection", detection_id, metadata={"domain": observation["domain"]})
        return self.detection(actor, detection_id)

    def _attach(self, actor: dict[str, Any], detection_id: str, observation: dict[str, Any], relation: str, similarity: float, rationale: str) -> None:
        stamp = now()
        link_id = stable_id("detection-observation", self._workspace(actor), observation["id"])
        with self.store.db() as connection:
            connection.execute(
                """INSERT INTO detection_observations(id,workspace_id,detection_id,observation_id,relation,source_family,similarity,rationale,created_at)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (link_id, self._workspace(actor), detection_id, observation["id"], relation, observation["source_family"], float(similarity), rationale, stamp),
            )
        self._link_claim(actor, detection_id, observation, relation, similarity)
        self.reassess(actor, detection_id, f"Observation {observation['id']} linked as {relation}")

    def correlate(self, actor: dict[str, Any], observation_id: str, force_new: bool = False) -> dict[str, Any]:
        workspace_id = self._workspace(actor)
        with self.store.db() as connection:
            linked = connection.execute(
                "SELECT detection_id,relation FROM detection_observations WHERE workspace_id=? AND observation_id=?",
                (workspace_id, observation_id),
            ).fetchone()
        if linked:
            return {"outcome": "already_processed", "relation": linked["relation"], "detection": self.detection(actor, linked["detection_id"])}
        observation = self._observation(actor, observation_id)
        if force_new:
            return {"outcome": "created", "relation": "ORIGINATES", "detection": self._create_detection(actor, observation)}
        candidates = []
        for candidate in self._candidate_rows(actor, observation):
            score, rationale = self._score(observation, candidate)
            explicit = self._explicit_relation(observation, candidate["id"])
            if explicit or score >= 0.58:
                candidates.append((1.0 if explicit else score, candidate, rationale))
        if not candidates:
            return {"outcome": "created", "relation": "ORIGINATES", "detection": self._create_detection(actor, observation)}
        score, detection, rationale = sorted(candidates, key=lambda item: item[0], reverse=True)[0]
        relation = self._relation(observation, detection, score)
        self._attach(actor, detection["id"], observation, relation, score, rationale)
        return {"outcome": "linked", "relation": relation, "similarity": score, "detection": self.detection(actor, detection["id"])}

    def process_pending(self, actor: dict[str, Any], limit: int = 100) -> dict[str, Any]:
        workspace_id = self._workspace(actor)
        with self.store.db() as connection:
            rows = connection.execute(
                """SELECT o.id FROM sensor_observations o LEFT JOIN detection_observations d
                ON d.workspace_id=o.workspace_id AND d.observation_id=o.id
                WHERE o.workspace_id=? AND d.id IS NULL ORDER BY o.observed_at LIMIT ?""",
                (workspace_id, max(1, min(1000, int(limit)))),
            ).fetchall()
        summary = {"processed": 0, "created": 0, "linked": 0, "duplicates": 0, "contradictions": 0}
        detection_ids = []
        for row in rows:
            result = self.correlate(actor, row["id"])
            summary["processed"] += 1
            summary["created" if result["outcome"] == "created" else "linked"] += 1
            if result["relation"] == "DUPLICATES":
                summary["duplicates"] += 1
            if result["relation"] == "CONTRADICTS":
                summary["contradictions"] += 1
            detection_ids.append(result["detection"]["id"])
        summary["detection_ids"] = sorted(set(detection_ids))
        return summary

    def reassess(self, actor: dict[str, Any], detection_id: str, reason: str = "Automated reassessment") -> dict[str, Any]:
        current = self.detection(actor, detection_id, include_links=False)
        with self.store.db() as connection:
            rows = connection.execute(
                "SELECT relation,source_family FROM detection_observations WHERE workspace_id=? AND detection_id=?",
                (self._workspace(actor), detection_id),
            ).fetchall()
        support: dict[str, float] = {}
        contradictions: dict[str, float] = {}
        duplicate_count = 0
        for row in rows:
            if row["relation"] == "DUPLICATES":
                duplicate_count += 1
                continue
            target = contradictions if row["relation"] == "CONTRADICTS" else support
            target[row["source_family"]] = max(target.get(row["source_family"], 0.0), 0.8)
        support_weight = sum(support.values())
        contradiction_weight = sum(contradictions.values())
        total = support_weight + contradiction_weight
        confidence = round(support_weight / (total + 0.5), 4) if total else 0.0
        if contradiction_weight >= 0.8 and contradiction_weight >= support_weight * 0.5:
            state = "DISPUTED"
        elif support_weight >= 1.5 and len(support) >= 2 and contradiction_weight < 0.5:
            state = "CONFIRMED"
        else:
            state = "OPEN"
        stamp = now()
        changed = current["state"] != state or abs(float(current["confidence"]) - confidence) > 0.0001
        with self.store.db() as connection:
            connection.execute(
                """UPDATE autonomous_detections SET state=?,confidence=?,source_families=?,supporting_observations=?,
                contradicting_observations=?,last_observed_at=(SELECT MAX(o.observed_at) FROM detection_observations d JOIN sensor_observations o ON o.id=d.observation_id WHERE d.detection_id=?),updated_at=?
                WHERE id=? AND workspace_id=?""",
                (state, confidence, len(set(support) | set(contradictions)), sum(1 for row in rows if row["relation"] in {"ORIGINATES", "CORROBORATES"}),
                 sum(1 for row in rows if row["relation"] == "CONTRADICTS"), detection_id, stamp, detection_id, self._workspace(actor)),
            )
            if changed:
                revision_id = stable_id("detection-revision", self._workspace(actor), detection_id, stamp, state, confidence)
                connection.execute(
                    """INSERT INTO detection_revisions(id,workspace_id,detection_id,from_state,to_state,from_confidence,to_confidence,reason,created_by,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (revision_id, self._workspace(actor), detection_id, current["state"], state, float(current["confidence"]), confidence, str(reason), self._actor_id(actor), stamp),
                )
        if changed:
            self.store.identity.audit(self._workspace(actor), self._actor_id(actor), "detection.reassessed", "detection", detection_id, metadata={"state": state, "confidence": confidence, "duplicates": duplicate_count})
        return self.detection(actor, detection_id)

    def review(self, actor: dict[str, Any], detection_id: str, review_state: str, reason: str) -> dict[str, Any]:
        review_state = str(review_state or "").strip().upper()
        if review_state not in VALID_REVIEW_STATES:
            raise ValueError("invalid review_state")
        current = self.detection(actor, detection_id, include_links=False)
        stamp = now()
        revision_id = stable_id("detection-review", self._workspace(actor), detection_id, stamp, review_state)
        with self.store.db() as connection:
            connection.execute(
                "UPDATE autonomous_detections SET review_state=?,updated_at=? WHERE id=? AND workspace_id=?",
                (review_state, stamp, detection_id, self._workspace(actor)),
            )
            connection.execute(
                """INSERT INTO detection_revisions(id,workspace_id,detection_id,from_state,to_state,from_confidence,to_confidence,reason,created_by,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (revision_id, self._workspace(actor), detection_id, current["effective_state"], review_state, float(current["confidence"]), float(current["confidence"]), str(reason or "Analyst review"), self._actor_id(actor), stamp),
            )
        self.store.identity.audit(self._workspace(actor), self._actor_id(actor), "detection.reviewed", "detection", detection_id, metadata={"review_state": review_state, "reason": reason})
        return self.detection(actor, detection_id)

    def detection(self, actor: dict[str, Any], detection_id: str, include_links: bool = True) -> dict[str, Any]:
        with self.store.db() as connection:
            row = connection.execute(
                "SELECT * FROM autonomous_detections WHERE id=? AND workspace_id=?",
                (detection_id, self._workspace(actor)),
            ).fetchone()
        if not row:
            raise KeyError("detection not found")
        item = dict(row)
        item["confidence"] = float(item["confidence"])
        item["effective_state"] = item.get("review_state") or item["state"]
        if include_links:
            with self.store.db() as connection:
                links = connection.execute(
                    """SELECT d.*,o.sensor_id,o.domain,o.external_id,o.observed_at,o.latitude,o.longitude,o.severity,o.title,o.payload
                    FROM detection_observations d JOIN sensor_observations o ON o.id=d.observation_id
                    WHERE d.workspace_id=? AND d.detection_id=? ORDER BY o.observed_at""",
                    (self._workspace(actor), detection_id),
                ).fetchall()
                revisions = connection.execute(
                    "SELECT * FROM detection_revisions WHERE workspace_id=? AND detection_id=? ORDER BY created_at",
                    (self._workspace(actor), detection_id),
                ).fetchall()
            item["observations"] = [{**dict(row), "payload": loads(row["payload"], {})} for row in links]
            item["revisions"] = [dict(row) for row in revisions]
            if item.get("claim_id"):
                item["claim"] = self.integrity.claim(actor, item["claim_id"])
        return item

    def detections(self, actor: dict[str, Any], state: str = "", domain: str = "", limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT id FROM autonomous_detections WHERE workspace_id=?"
        args: list[Any] = [self._workspace(actor)]
        if state:
            sql += " AND COALESCE(review_state,state)=?"
            args.append(str(state).strip().upper())
        if domain:
            sql += " AND domain=?"
            args.append(str(domain).strip().lower())
        sql += " ORDER BY updated_at DESC LIMIT ?"
        args.append(max(1, min(500, int(limit))))
        with self.store.db() as connection:
            rows = connection.execute(sql, args).fetchall()
        return [self.detection(actor, row["id"], include_links=False) for row in rows]

    def scorecard(self, actor: dict[str, Any]) -> dict[str, Any]:
        workspace_id = self._workspace(actor)
        with self.store.db() as connection:
            states = connection.execute(
                "SELECT COALESCE(review_state,state) effective_state,COUNT(*) total FROM autonomous_detections WHERE workspace_id=? GROUP BY COALESCE(review_state,state)",
                (workspace_id,),
            ).fetchall()
            relations = connection.execute(
                "SELECT relation,COUNT(*) total FROM detection_observations WHERE workspace_id=? GROUP BY relation",
                (workspace_id,),
            ).fetchall()
            pending = connection.execute(
                """SELECT COUNT(*) FROM sensor_observations o LEFT JOIN detection_observations d
                ON d.workspace_id=o.workspace_id AND d.observation_id=o.id WHERE o.workspace_id=? AND d.id IS NULL""",
                (workspace_id,),
            ).fetchone()[0]
        return {
            "phase": 16,
            "detections_by_state": {row["effective_state"]: int(row["total"]) for row in states},
            "observation_relations": {row["relation"]: int(row["total"]) for row in relations},
            "pending_observations": int(pending),
        }
