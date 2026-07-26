from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from typing import Any

from phase26_operations import (
    _format_timestamp,
    _parse_timestamp,
    _reject_future_timestamp,
)
from storage import now, sid


SUBJECT_TYPES = {"SOURCE", "DETECTION", "ANALYST", "RULE", "TRIGGER"}
OUTCOMES = {
    "TRUE_POSITIVE",
    "FALSE_POSITIVE",
    "FALSE_NEGATIVE",
    "TRUE_NEGATIVE",
    "CORRECT",
    "INCORRECT",
}
POSITIVE_OUTCOMES = {"TRUE_POSITIVE", "TRUE_NEGATIVE", "CORRECT"}
REFERENCE_KEYS = {
    "artifact",
    "checksum",
    "report",
    "report_url",
    "run_id",
    "sha256",
    "url",
}
STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "to",
    "was",
    "were",
    "with",
}


def _json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )


def _load(value: Any, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except (TypeError, json.JSONDecodeError):
        return default


def _normal(value: Any) -> str:
    text = re.sub(r"https?://\S+", " ", str(value or "").lower())
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return " ".join(text.split())


def _tokens(value: Any) -> set[str]:
    return {
        token
        for token in _normal(value).split()
        if len(token) > 2 and token not in STOPWORDS
    }


def _jaccard(left: Any, right: Any) -> float:
    a, b = _tokens(left), _tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _bounded_number(
    value: Any, field: str, minimum: float, maximum: float
) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ValueError(
            f"{field} must be between {minimum:g} and {maximum:g}"
        )
    return number


def _evidence(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or not any(
        str(value.get(key) or "").strip() for key in REFERENCE_KEYS
    ):
        raise ValueError("evidence must contain a durable reference")
    return value


class AccuracyHistory:
    """Immutable operational outcomes and deterministic historical retrieval."""

    def __init__(self, store, integrity, detection, forecasts):
        self.store = store
        self.integrity = integrity
        self.detection = detection
        self.forecasts = forecasts
        self.init()

    def init(self) -> None:
        with self.store.db() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS accuracy_outcomes(
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    subject_type TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    score REAL NOT NULL,
                    weight REAL NOT NULL,
                    domain TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    actor_user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(workspace_id,fingerprint)
                );
                CREATE TABLE IF NOT EXISTS historical_cases(
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    canonical_key TEXT NOT NULL,
                    title TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    features TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    actor_user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(workspace_id,canonical_key)
                );
                CREATE TABLE IF NOT EXISTS syndication_fingerprints(
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    normalized_text TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(workspace_id,content_hash)
                );
                CREATE TABLE IF NOT EXISTS syndication_occurrences(
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    fingerprint_id TEXT NOT NULL,
                    source_origin_id TEXT NOT NULL,
                    lineage_key TEXT NOT NULL,
                    url TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    actor_user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(
                        workspace_id,fingerprint_id,source_origin_id,url
                    )
                );
                CREATE INDEX IF NOT EXISTS idx_accuracy_outcomes_subject
                    ON accuracy_outcomes(
                        workspace_id,subject_type,subject_id,observed_at
                    );
                CREATE INDEX IF NOT EXISTS idx_accuracy_outcomes_domain
                    ON accuracy_outcomes(workspace_id,domain,observed_at);
                CREATE INDEX IF NOT EXISTS idx_historical_cases_domain
                    ON historical_cases(workspace_id,domain,observed_at);
                CREATE INDEX IF NOT EXISTS idx_syndication_occurrences
                    ON syndication_occurrences(
                        workspace_id,fingerprint_id,lineage_key
                    );
                """
            )

    @staticmethod
    def _workspace(actor: dict[str, Any]) -> str:
        return str(actor["workspace_id"])

    @staticmethod
    def _actor(actor: dict[str, Any]) -> str:
        return str(actor.get("id") or "system")

    def _write(self, actor: dict[str, Any]) -> None:
        self.store.identity.require(actor, "write")

    @staticmethod
    def _timestamp(value: Any, field: str = "observed_at") -> str:
        try:
            parsed = _parse_timestamp(value or now())
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{field} must be a timezone-aware ISO-8601 timestamp"
            ) from exc
        _reject_future_timestamp(parsed, field)
        return _format_timestamp(parsed)

    def _validate_subject(
        self, actor: dict[str, Any], subject_type: str, subject_id: str
    ) -> None:
        workspace = self._workspace(actor)
        table = {
            "SOURCE": "source_origins",
            "DETECTION": "autonomous_detections",
        }.get(subject_type)
        if table:
            with self.store.db() as connection:
                row = connection.execute(
                    f"SELECT 1 FROM {table} WHERE id=? AND workspace_id=?",
                    (subject_id, workspace),
                ).fetchone()
            if not row:
                raise KeyError(f"{subject_type.lower()} not found")
            return
        if subject_type == "ANALYST":
            with self.store.db() as connection:
                row = connection.execute(
                    """
                    SELECT 1 FROM memberships
                    WHERE user_id=? AND workspace_id=?
                    """,
                    (subject_id, workspace),
                ).fetchone()
            if not row:
                raise KeyError("analyst not found")

    def record_outcome(
        self, actor: dict[str, Any], payload: dict[str, Any]
    ) -> dict[str, Any]:
        self._write(actor)
        subject_type = str(payload.get("subject_type") or "").upper()
        subject_id = str(payload.get("subject_id") or "").strip()
        outcome = str(payload.get("outcome") or "").upper()
        if subject_type not in SUBJECT_TYPES:
            raise ValueError("invalid subject_type")
        if not subject_id:
            raise ValueError("subject_id required")
        if outcome not in OUTCOMES:
            raise ValueError("invalid outcome")
        self._validate_subject(actor, subject_type, subject_id)
        evidence = _evidence(payload.get("evidence"))
        score = _bounded_number(
            payload.get(
                "score", 1.0 if outcome in POSITIVE_OUTCOMES else 0.0
            ),
            "score",
            0.0,
            1.0,
        )
        weight = _bounded_number(
            payload.get("weight", 1.0), "weight", 0.01, 100.0
        )
        domain = _normal(payload.get("domain") or "general")[:80]
        observed = self._timestamp(payload.get("observed_at"))
        metadata = payload.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise ValueError("metadata must be an object")
        fingerprint = hashlib.sha256(
            _json(
                {
                    "workspace": self._workspace(actor),
                    "subject_type": subject_type,
                    "subject_id": subject_id,
                    "outcome": outcome,
                    "score": score,
                    "weight": weight,
                    "domain": domain,
                    "evidence": evidence,
                    "observed_at": observed,
                }
            ).encode("utf-8")
        ).hexdigest()
        outcome_id = sid("accuracy-outcome", self._workspace(actor), fingerprint)
        created = now()
        inserted = False
        with self.store.db() as connection:
            existing = connection.execute(
                """
                SELECT id FROM accuracy_outcomes
                WHERE workspace_id=? AND fingerprint=?
                """,
                (self._workspace(actor), fingerprint),
            ).fetchone()
            if not existing:
                connection.execute(
                    """
                    INSERT INTO accuracy_outcomes(
                        id,workspace_id,fingerprint,subject_type,subject_id,
                        outcome,score,weight,domain,evidence,metadata,
                        observed_at,actor_user_id,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        outcome_id,
                        self._workspace(actor),
                        fingerprint,
                        subject_type,
                        subject_id,
                        outcome,
                        score,
                        weight,
                        domain,
                        _json(evidence),
                        _json(metadata),
                        observed,
                        self._actor(actor),
                        created,
                    ),
                )
                inserted = True
            else:
                outcome_id = existing["id"]
        if inserted:
            self.store.identity.audit(
                self._workspace(actor),
                self._actor(actor),
                "accuracy.outcome_recorded",
                "accuracy_outcome",
                outcome_id,
                metadata={
                    "subject_type": subject_type,
                    "subject_id": subject_id,
                    "outcome": outcome,
                },
            )
        item = self.outcome(actor, outcome_id)
        item["duplicate"] = not inserted
        return item

    @staticmethod
    def _outcome_item(row: Any) -> dict[str, Any]:
        item = dict(row)
        item["score"] = float(item["score"])
        item["weight"] = float(item["weight"])
        item["evidence"] = _load(item["evidence"], {})
        item["metadata"] = _load(item["metadata"], {})
        return item

    def outcome(
        self, actor: dict[str, Any], outcome_id: str
    ) -> dict[str, Any]:
        with self.store.db() as connection:
            row = connection.execute(
                """
                SELECT * FROM accuracy_outcomes
                WHERE id=? AND workspace_id=?
                """,
                (outcome_id, self._workspace(actor)),
            ).fetchone()
        if not row:
            raise KeyError("accuracy outcome not found")
        return self._outcome_item(row)

    def outcomes(
        self,
        actor: dict[str, Any],
        subject_type: str = "",
        subject_id: str = "",
        domain: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM accuracy_outcomes WHERE workspace_id=?"
        args: list[Any] = [self._workspace(actor)]
        if subject_type:
            subject_type = subject_type.upper()
            if subject_type not in SUBJECT_TYPES:
                raise ValueError("invalid subject_type")
            sql += " AND subject_type=?"
            args.append(subject_type)
        if subject_id:
            sql += " AND subject_id=?"
            args.append(subject_id)
        if domain:
            sql += " AND domain=?"
            args.append(_normal(domain)[:80])
        sql += " ORDER BY observed_at DESC,created_at DESC LIMIT ?"
        args.append(max(1, min(500, int(limit))))
        with self.store.db() as connection:
            rows = connection.execute(sql, args).fetchall()
        return [self._outcome_item(row) for row in rows]

    @staticmethod
    def _rates(rows: list[dict[str, Any]]) -> dict[str, Any]:
        counts = {name: 0 for name in sorted(OUTCOMES)}
        weighted_score = 0.0
        total_weight = 0.0
        for row in rows:
            counts[row["outcome"]] += 1
            weighted_score += float(row["score"]) * float(row["weight"])
            total_weight += float(row["weight"])
        tp = counts["TRUE_POSITIVE"]
        fp = counts["FALSE_POSITIVE"]
        fn = counts["FALSE_NEGATIVE"]
        precision = None if tp + fp == 0 else tp / (tp + fp)
        recall = None if tp + fn == 0 else tp / (tp + fn)
        return {
            "count": len(rows),
            "counts": counts,
            "weighted_accuracy": (
                None if not total_weight else weighted_score / total_weight
            ),
            "smoothed_accuracy": (
                None
                if not total_weight
                else (weighted_score + 1.0) / (total_weight + 2.0)
            ),
            "precision": precision,
            "recall": recall,
        }

    def scorecard(
        self, actor: dict[str, Any], domain: str = ""
    ) -> dict[str, Any]:
        sql = """
            SELECT subject_type,subject_id,outcome,score,weight
            FROM accuracy_outcomes WHERE workspace_id=?
        """
        args: list[Any] = [self._workspace(actor)]
        if domain:
            sql += " AND domain=?"
            args.append(_normal(domain)[:80])
        with self.store.db() as connection:
            rows = [
                dict(row) for row in connection.execute(sql, args).fetchall()
            ]
        by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
        by_subject: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(
            list
        )
        for row in rows:
            by_type[row["subject_type"]].append(row)
            by_subject[(row["subject_type"], row["subject_id"])].append(row)
        subjects = [
            {
                "subject_type": key[0],
                "subject_id": key[1],
                **self._rates(values),
            }
            for key, values in sorted(by_subject.items())
        ]
        return {
            "phase": 28,
            "workspace_id": self._workspace(actor),
            "domain": _normal(domain)[:80] if domain else "",
            "overall": self._rates(rows),
            "by_subject_type": {
                key: self._rates(values)
                for key, values in sorted(by_type.items())
            },
            "subjects": subjects,
            "forecast_calibration": self.forecasts.metrics(actor),
            "methodology": {
                "append_only_outcomes": True,
                "same_evidence_is_idempotent": True,
                "weighted_accuracy": "sum(score * weight) / sum(weight)",
                "smoothed_accuracy": "(weighted_score + 1) / (weight + 2)",
                "forecast_source": "Phase 11 canonical resolved forecasts",
                "external_ai_required": False,
            },
        }

    def record_case(
        self, actor: dict[str, Any], payload: dict[str, Any]
    ) -> dict[str, Any]:
        self._write(actor)
        title = str(payload.get("title") or "").strip()
        outcome = str(payload.get("outcome") or "").strip().upper()
        domain = _normal(payload.get("domain") or "general")[:80]
        features = payload.get("features") or {}
        if not title:
            raise ValueError("title required")
        if not outcome or len(outcome) > 64:
            raise ValueError("outcome required")
        if not isinstance(features, dict):
            raise ValueError("features must be an object")
        evidence = _evidence(payload.get("evidence"))
        observed = self._timestamp(payload.get("observed_at"))
        canonical_key = str(payload.get("canonical_key") or "").strip()
        if not canonical_key:
            canonical_key = hashlib.sha256(
                _json(
                    {
                        "title": _normal(title),
                        "domain": domain,
                        "outcome": outcome,
                        "observed_at": observed,
                    }
                ).encode("utf-8")
            ).hexdigest()
        case_id = sid(
            "historical-case", self._workspace(actor), canonical_key
        )
        values = (
            case_id,
            self._workspace(actor),
            canonical_key,
            title,
            domain,
            outcome,
            str(payload.get("summary") or "").strip(),
            _json(features),
            _json(evidence),
            observed,
            self._actor(actor),
            now(),
        )
        inserted = False
        with self.store.db() as connection:
            existing = connection.execute(
                """
                SELECT id,title,domain,outcome,summary,features,evidence,
                    observed_at
                FROM historical_cases
                WHERE workspace_id=? AND canonical_key=?
                """,
                (self._workspace(actor), canonical_key),
            ).fetchone()
            if existing and (
                existing["title"] != title
                or existing["domain"] != domain
                or existing["outcome"] != outcome
                or existing["summary"]
                != str(payload.get("summary") or "").strip()
                or existing["features"] != _json(features)
                or existing["evidence"] != _json(evidence)
                or existing["observed_at"] != observed
            ):
                raise ValueError("canonical_key already identifies another case")
            if not existing:
                connection.execute(
                    """
                    INSERT INTO historical_cases(
                        id,workspace_id,canonical_key,title,domain,outcome,
                        summary,features,evidence,observed_at,actor_user_id,
                        created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    values,
                )
                inserted = True
        if inserted:
            self.store.identity.audit(
                self._workspace(actor),
                self._actor(actor),
                "accuracy.case_recorded",
                "historical_case",
                case_id,
                metadata={"domain": domain, "outcome": outcome},
            )
        item = self.case(actor, case_id)
        item["duplicate"] = not inserted
        return item

    @staticmethod
    def _case_item(row: Any) -> dict[str, Any]:
        item = dict(row)
        item["features"] = _load(item["features"], {})
        item["evidence"] = _load(item["evidence"], {})
        return item

    def case(self, actor: dict[str, Any], case_id: str) -> dict[str, Any]:
        with self.store.db() as connection:
            row = connection.execute(
                """
                SELECT * FROM historical_cases
                WHERE id=? AND workspace_id=?
                """,
                (case_id, self._workspace(actor)),
            ).fetchone()
        if not row:
            raise KeyError("historical case not found")
        return self._case_item(row)

    def cases(
        self, actor: dict[str, Any], domain: str = "", limit: int = 100
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM historical_cases WHERE workspace_id=?"
        args: list[Any] = [self._workspace(actor)]
        if domain:
            sql += " AND domain=?"
            args.append(_normal(domain)[:80])
        sql += " ORDER BY observed_at DESC,created_at DESC LIMIT ?"
        args.append(max(1, min(500, int(limit))))
        with self.store.db() as connection:
            rows = connection.execute(sql, args).fetchall()
        return [self._case_item(row) for row in rows]

    def analogs(
        self,
        actor: dict[str, Any],
        query: str,
        domain: str = "",
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        query = str(query or "").strip()
        if not query:
            raise ValueError("query required")
        candidates = self.cases(actor, domain=domain, limit=500)
        output = []
        for case in candidates:
            searchable = " ".join(
                (
                    case["title"],
                    case["summary"],
                    _json(case["features"]),
                )
            )
            text_score = _jaccard(query, searchable)
            domain_score = (
                1.0
                if domain and case["domain"] == _normal(domain)[:80]
                else 0.0
            )
            score = round(0.9 * text_score + 0.1 * domain_score, 6)
            if score > 0:
                output.append({**case, "similarity": score})
        output.sort(
            key=lambda item: (
                -item["similarity"],
                item["canonical_key"],
            )
        )
        return output[: max(1, min(100, int(limit)))]

    def record_fingerprint(
        self, actor: dict[str, Any], payload: dict[str, Any]
    ) -> dict[str, Any]:
        self._write(actor)
        source_id = str(payload.get("source_origin_id") or "").strip()
        url = str(payload.get("url") or "").strip()
        if not source_id:
            raise ValueError("source_origin_id required")
        if not url:
            raise ValueError("url required")
        source = self.integrity.source(actor, source_id)
        evidence = _evidence(payload.get("evidence"))
        normalized = _normal(payload.get("content"))
        content_hash = str(payload.get("content_hash") or "").lower().strip()
        if normalized:
            calculated = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            if content_hash and content_hash != calculated:
                raise ValueError("content_hash does not match content")
            content_hash = calculated
        if not re.fullmatch(r"[0-9a-f]{64}", content_hash):
            raise ValueError("content or a SHA-256 content_hash is required")
        published = self._timestamp(
            payload.get("published_at"), "published_at"
        )
        fingerprint_id = sid(
            "syndication", self._workspace(actor), content_hash
        )
        occurrence_id = sid(
            "syndication-occurrence",
            self._workspace(actor),
            fingerprint_id,
            source_id,
            url,
        )
        created = now()
        fingerprint_created = False
        occurrence_created = False
        with self.store.db() as connection:
            existing = connection.execute(
                """
                SELECT id FROM syndication_fingerprints
                WHERE workspace_id=? AND content_hash=?
                """,
                (self._workspace(actor), content_hash),
            ).fetchone()
            if not existing:
                connection.execute(
                    """
                    INSERT INTO syndication_fingerprints(
                        id,workspace_id,content_hash,normalized_text,
                        first_seen_at,created_at
                    ) VALUES(?,?,?,?,?,?)
                    """,
                    (
                        fingerprint_id,
                        self._workspace(actor),
                        content_hash,
                        normalized,
                        published,
                        created,
                    ),
                )
                fingerprint_created = True
            else:
                fingerprint_id = existing["id"]
            occurrence = connection.execute(
                """
                SELECT id FROM syndication_occurrences
                WHERE workspace_id=? AND fingerprint_id=?
                    AND source_origin_id=? AND url=?
                """,
                (self._workspace(actor), fingerprint_id, source_id, url),
            ).fetchone()
            if not occurrence:
                connection.execute(
                    """
                    INSERT INTO syndication_occurrences(
                        id,workspace_id,fingerprint_id,source_origin_id,
                        lineage_key,url,published_at,evidence,actor_user_id,
                        created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        occurrence_id,
                        self._workspace(actor),
                        fingerprint_id,
                        source_id,
                        source["lineage_key"],
                        url,
                        published,
                        _json(evidence),
                        self._actor(actor),
                        created,
                    ),
                )
                occurrence_created = True
            else:
                occurrence_id = occurrence["id"]
        if occurrence_created:
            self.store.identity.audit(
                self._workspace(actor),
                self._actor(actor),
                "accuracy.syndication_observed",
                "syndication_fingerprint",
                fingerprint_id,
                metadata={
                    "source_origin_id": source_id,
                    "lineage_key": source["lineage_key"],
                    "new_fingerprint": fingerprint_created,
                },
            )
        return self.fingerprint(actor, fingerprint_id) | {
            "duplicate_content": not fingerprint_created,
            "duplicate_occurrence": not occurrence_created,
            "occurrence_id": occurrence_id,
        }

    def fingerprint(
        self, actor: dict[str, Any], fingerprint_id: str
    ) -> dict[str, Any]:
        workspace = self._workspace(actor)
        with self.store.db() as connection:
            row = connection.execute(
                """
                SELECT * FROM syndication_fingerprints
                WHERE id=? AND workspace_id=?
                """,
                (fingerprint_id, workspace),
            ).fetchone()
            occurrences = connection.execute(
                """
                SELECT * FROM syndication_occurrences
                WHERE fingerprint_id=? AND workspace_id=?
                ORDER BY published_at,created_at
                """,
                (fingerprint_id, workspace),
            ).fetchall()
        if not row:
            raise KeyError("syndication fingerprint not found")
        output = dict(row)
        output["occurrences"] = []
        lineages = set()
        for occurrence in occurrences:
            item = dict(occurrence)
            item["evidence"] = _load(item["evidence"], {})
            output["occurrences"].append(item)
            lineages.add(item["lineage_key"])
        output["occurrence_count"] = len(output["occurrences"])
        output["independent_lineage_count"] = len(lineages)
        return output

    def fingerprints(
        self, actor: dict[str, Any], limit: int = 100
    ) -> list[dict[str, Any]]:
        with self.store.db() as connection:
            rows = connection.execute(
                """
                SELECT id FROM syndication_fingerprints
                WHERE workspace_id=?
                ORDER BY first_seen_at DESC LIMIT ?
                """,
                (self._workspace(actor), max(1, min(500, int(limit)))),
            ).fetchall()
        return [self.fingerprint(actor, row["id"]) for row in rows]
