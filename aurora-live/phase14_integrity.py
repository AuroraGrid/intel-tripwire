from __future__ import annotations

import hashlib
import json
import re
import secrets
import urllib.parse
from datetime import datetime, timezone

from storage import sid

CLAIM_TYPES = {"FACT", "INFERENCE", "FORECAST", "SPECULATION", "UNVERIFIED_CLAIM"}
CLAIM_STATUSES = {"SUPPORTED", "PLAUSIBLE", "NOT_PROVEN", "DISPUTED", "RETRACTED"}
RELATIONS = {"SUPPORTS", "CONTRADICTS", "CONTEXTUALIZES", "DUPLICATES", "SUPERSEDES"}
TIER_WEIGHTS = {1: 1.0, 2: 0.8, 3: 0.6, 4: 0.35, 5: 0.1}


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def loads(value, default):
    try:
        return json.loads(value) if value else default
    except (TypeError, json.JSONDecodeError):
        return default


def normalize_text(value):
    text = str(value or "").lower()
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return " ".join(text.split())


def content_hash(value):
    return hashlib.sha256(normalize_text(value).encode("utf-8")).hexdigest()


def host(value):
    parsed = urllib.parse.urlparse(str(value or ""))
    return (parsed.hostname or "").lower().removeprefix("www.")


class EvidenceIntegrity:
    """Zero-trust claim, provenance, contradiction and correction engine."""

    def __init__(self, store):
        self.store = store
        self.init()

    def init(self):
        with self.store.db() as connection:
            connection.executescript("""
            CREATE TABLE IF NOT EXISTS source_origins(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, name TEXT NOT NULL,
                owner TEXT NOT NULL, canonical_url TEXT NOT NULL, lineage_key TEXT NOT NULL,
                source_tier INTEGER NOT NULL, method TEXT NOT NULL, jurisdiction TEXT NOT NULL,
                reliability REAL NOT NULL, created_at TEXT NOT NULL,
                UNIQUE(workspace_id,name)
            );
            CREATE TABLE IF NOT EXISTS atomic_claims(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, statement TEXT NOT NULL,
                claim_type TEXT NOT NULL, status TEXT NOT NULL, confidence REAL NOT NULL,
                scope TEXT NOT NULL, falsifier TEXT NOT NULL, created_by TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS claim_evidence(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, claim_id TEXT NOT NULL,
                source_origin_id TEXT NOT NULL, title TEXT NOT NULL, url TEXT NOT NULL,
                published_at TEXT NOT NULL, retrieved_at TEXT NOT NULL, relation TEXT NOT NULL,
                content_hash TEXT NOT NULL, normalized_text TEXT NOT NULL,
                duplicate_of TEXT, metadata TEXT NOT NULL, created_by TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS claim_revisions(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, claim_id TEXT NOT NULL,
                from_status TEXT NOT NULL, to_status TEXT NOT NULL,
                from_confidence REAL NOT NULL, to_confidence REAL NOT NULL,
                reason TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS contradiction_queue(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, claim_id TEXT NOT NULL,
                evidence_id TEXT NOT NULL, severity TEXT NOT NULL, state TEXT NOT NULL,
                reason TEXT NOT NULL, created_at TEXT NOT NULL, resolved_at TEXT,
                resolved_by TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_sources_workspace ON source_origins(workspace_id,source_tier,name);
            CREATE INDEX IF NOT EXISTS idx_claims_workspace ON atomic_claims(workspace_id,updated_at,status);
            CREATE INDEX IF NOT EXISTS idx_claim_evidence_claim ON claim_evidence(workspace_id,claim_id,created_at);
            CREATE INDEX IF NOT EXISTS idx_claim_evidence_hash ON claim_evidence(workspace_id,claim_id,content_hash);
            CREATE INDEX IF NOT EXISTS idx_contradictions_state ON contradiction_queue(workspace_id,state,created_at);
            """)

    def register_source(self, actor, payload):
        name = str(payload.get("name", "")).strip()
        if not name:
            raise ValueError("name required")
        canonical_url = str(payload.get("canonical_url", "")).strip()
        source_tier = int(payload.get("source_tier", 4))
        reliability = float(payload.get("reliability", 0.5))
        if source_tier not in TIER_WEIGHTS:
            raise ValueError("source_tier must be between 1 and 5")
        if not 0 <= reliability <= 1:
            raise ValueError("reliability must be between 0 and 1")
        lineage_key = str(payload.get("lineage_key", "")).strip().lower()
        if not lineage_key:
            lineage_key = host(canonical_url) or normalize_text(name).replace(" ", "-")
        source_id = sid("source-origin", actor["workspace_id"], name.lower())
        values = (
            source_id, actor["workspace_id"], name, str(payload.get("owner", "")).strip(),
            canonical_url, lineage_key, source_tier, str(payload.get("method", "")).strip(),
            str(payload.get("jurisdiction", "")).strip(), reliability, now(),
        )
        with self.store.db() as connection:
            connection.execute("""INSERT INTO source_origins(id,workspace_id,name,owner,canonical_url,lineage_key,source_tier,method,jurisdiction,reliability,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(workspace_id,name) DO UPDATE SET
            owner=excluded.owner,canonical_url=excluded.canonical_url,lineage_key=excluded.lineage_key,
            source_tier=excluded.source_tier,method=excluded.method,jurisdiction=excluded.jurisdiction,
            reliability=excluded.reliability""", values)
        self.store.identity.audit(actor["workspace_id"], actor["id"], "source.registered", "source_origin", source_id, metadata={"tier": source_tier, "lineage_key": lineage_key})
        return self.source(actor, source_id)

    def source(self, actor, source_id):
        with self.store.db() as connection:
            row = connection.execute("SELECT * FROM source_origins WHERE id=? AND workspace_id=?", (source_id, actor["workspace_id"])).fetchone()
        if not row:
            raise KeyError("source not found")
        item = dict(row)
        item["source_tier"] = int(item["source_tier"])
        item["reliability"] = float(item["reliability"])
        return item

    def sources(self, actor):
        with self.store.db() as connection:
            rows = connection.execute("SELECT id FROM source_origins WHERE workspace_id=? ORDER BY source_tier,name", (actor["workspace_id"],)).fetchall()
        return [self.source(actor, row["id"]) for row in rows]

    def create_claim(self, actor, payload):
        statement = str(payload.get("statement", "")).strip()
        claim_type = str(payload.get("claim_type", "UNVERIFIED_CLAIM")).strip().upper()
        if not statement:
            raise ValueError("statement required")
        if claim_type not in CLAIM_TYPES:
            raise ValueError("invalid claim_type")
        stamp = now()
        claim_id = sid("atomic-claim", actor["workspace_id"], statement, stamp, secrets.token_hex(4))
        with self.store.db() as connection:
            connection.execute("INSERT INTO atomic_claims(id,workspace_id,statement,claim_type,status,confidence,scope,falsifier,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (claim_id, actor["workspace_id"], statement, claim_type, "NOT_PROVEN", 0.0,
                 str(payload.get("scope", "")).strip(), str(payload.get("falsifier", "")).strip(),
                 actor["id"], stamp, stamp))
        self.store.identity.audit(actor["workspace_id"], actor["id"], "claim.created", "atomic_claim", claim_id, metadata={"claim_type": claim_type})
        return self.claim(actor, claim_id)

    def claims(self, actor, status="", claim_type="", limit=100):
        sql = "SELECT id FROM atomic_claims WHERE workspace_id=?"
        args = [actor["workspace_id"]]
        if status:
            status = status.upper()
            if status not in CLAIM_STATUSES:
                raise ValueError("invalid status")
            sql += " AND status=?"; args.append(status)
        if claim_type:
            claim_type = claim_type.upper()
            if claim_type not in CLAIM_TYPES:
                raise ValueError("invalid claim_type")
            sql += " AND claim_type=?"; args.append(claim_type)
        sql += " ORDER BY updated_at DESC LIMIT ?"; args.append(max(1, min(500, int(limit))))
        with self.store.db() as connection:
            rows = connection.execute(sql, args).fetchall()
        return [self.claim(actor, row["id"], include_evidence=False) for row in rows]

    def claim(self, actor, claim_id, include_evidence=True):
        with self.store.db() as connection:
            row = connection.execute("SELECT * FROM atomic_claims WHERE id=? AND workspace_id=?", (claim_id, actor["workspace_id"])).fetchone()
        if not row:
            raise KeyError("claim not found")
        item = dict(row)
        item["confidence"] = float(item["confidence"])
        if include_evidence:
            item["evidence"] = self.evidence(actor, claim_id)
            item["revisions"] = self.revisions(actor, claim_id)
            item["assessment"] = self.assessment(actor, claim_id, persist=False)
        return item

    def add_evidence(self, actor, claim_id, payload):
        self.claim(actor, claim_id, include_evidence=False)
        source_id = str(payload.get("source_origin_id", "")).strip()
        self.source(actor, source_id)
        title = str(payload.get("title", "")).strip()
        url = str(payload.get("url", "")).strip()
        text = str(payload.get("text") or title).strip()
        relation = str(payload.get("relation", "SUPPORTS")).strip().upper()
        if not title:
            raise ValueError("title required")
        if relation not in RELATIONS:
            raise ValueError("invalid relation")
        fingerprint = content_hash(text)
        duplicate_of = None
        with self.store.db() as connection:
            duplicate = connection.execute("SELECT id FROM claim_evidence WHERE workspace_id=? AND claim_id=? AND content_hash=? ORDER BY created_at LIMIT 1",
                (actor["workspace_id"], claim_id, fingerprint)).fetchone()
        if duplicate:
            relation = "DUPLICATES"
            duplicate_of = duplicate["id"]
        stamp = now()
        evidence_id = sid("claim-evidence", actor["workspace_id"], claim_id, source_id, fingerprint, stamp, secrets.token_hex(4))
        with self.store.db() as connection:
            connection.execute("""INSERT INTO claim_evidence(id,workspace_id,claim_id,source_origin_id,title,url,published_at,retrieved_at,relation,content_hash,normalized_text,duplicate_of,metadata,created_by,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (evidence_id, actor["workspace_id"], claim_id, source_id, title, url,
                 str(payload.get("published_at") or stamp), str(payload.get("retrieved_at") or stamp), relation,
                 fingerprint, normalize_text(text), duplicate_of, dumps(payload.get("metadata") or {}), actor["id"], stamp))
            if relation == "CONTRADICTS":
                queue_id = sid("contradiction", actor["workspace_id"], claim_id, evidence_id)
                connection.execute("INSERT INTO contradiction_queue(id,workspace_id,claim_id,evidence_id,severity,state,reason,created_at) VALUES(?,?,?,?,?,?,?,?)",
                    (queue_id, actor["workspace_id"], claim_id, evidence_id,
                     str(payload.get("severity", "high")).strip().lower(), "OPEN",
                     str(payload.get("reason") or "Evidence directly contradicts the atomic claim."), stamp))
        self.store.identity.audit(actor["workspace_id"], actor["id"], "evidence.linked", "claim_evidence", evidence_id, metadata={"claim_id": claim_id, "relation": relation, "duplicate_of": duplicate_of})
        return self.evidence_item(actor, evidence_id)

    def evidence_item(self, actor, evidence_id):
        with self.store.db() as connection:
            row = connection.execute("""SELECT e.*,s.name source_name,s.owner source_owner,s.lineage_key,s.source_tier,s.method,s.jurisdiction,s.reliability
            FROM claim_evidence e JOIN source_origins s ON s.id=e.source_origin_id
            WHERE e.id=? AND e.workspace_id=?""", (evidence_id, actor["workspace_id"])).fetchone()
        if not row:
            raise KeyError("evidence not found")
        item = dict(row)
        item["metadata"] = loads(item["metadata"], {})
        item["source_tier"] = int(item["source_tier"])
        item["reliability"] = float(item["reliability"])
        return item

    def evidence(self, actor, claim_id):
        with self.store.db() as connection:
            rows = connection.execute("SELECT id FROM claim_evidence WHERE workspace_id=? AND claim_id=? ORDER BY created_at", (actor["workspace_id"], claim_id)).fetchall()
        return [self.evidence_item(actor, row["id"]) for row in rows]

    def revisions(self, actor, claim_id):
        with self.store.db() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM claim_revisions WHERE workspace_id=? AND claim_id=? ORDER BY created_at", (actor["workspace_id"], claim_id)).fetchall()]

    def assessment(self, actor, claim_id, persist=True, reason="Automated zero-trust reassessment"):
        claim = self.claim(actor, claim_id, include_evidence=False)
        records = self.evidence(actor, claim_id)
        supports, contradicts = {}, {}
        duplicates = 0
        best_tier = 5
        for record in records:
            if record["relation"] == "DUPLICATES":
                duplicates += 1
                continue
            best_tier = min(best_tier, record["source_tier"])
            weight = TIER_WEIGHTS[record["source_tier"]] * record["reliability"]
            bucket = supports if record["relation"] in {"SUPPORTS", "SUPERSEDES"} else contradicts if record["relation"] == "CONTRADICTS" else None
            if bucket is not None:
                bucket[record["lineage_key"]] = max(bucket.get(record["lineage_key"], 0.0), weight)
        support_weight = round(sum(supports.values()), 4)
        contradiction_weight = round(sum(contradicts.values()), 4)
        origin_count = len(set(supports) | set(contradicts))
        total_weight = support_weight + contradiction_weight
        confidence = round(total_weight / (total_weight + 0.5), 4) if total_weight else 0.0
        if contradiction_weight >= 0.8 and contradiction_weight >= support_weight * 0.5:
            status = "DISPUTED"
        elif support_weight >= 1.4 and len(supports) >= 2 and contradiction_weight < 0.5:
            status = "SUPPORTED"
        elif support_weight >= 0.6 and support_weight > contradiction_weight:
            status = "PLAUSIBLE"
        else:
            status = "NOT_PROVEN"
        if claim["status"] == "RETRACTED":
            status = "RETRACTED"
        if status == "SUPPORTED" and confidence >= 0.8 and best_tier == 1:
            grade = "G3"
        elif confidence >= 0.6 and origin_count >= 2:
            grade = "G2"
        else:
            grade = "G1"
        counter = "No material contradiction is recorded."
        contradicted = [record for record in records if record["relation"] == "CONTRADICTS"]
        if contradicted:
            strongest = sorted(contradicted, key=lambda item: (item["source_tier"], -item["reliability"]))[0]
            counter = f"{strongest['source_name']}: {strongest['title']}"
        elif len(supports) < 2:
            counter = "The claim lacks two independent origin chains."
        result = {
            "claim_id": claim_id, "status": status, "confidence": confidence, "grade": grade,
            "report_count": len(records), "origin_count": origin_count, "duplicate_count": duplicates,
            "support_origins": len(supports), "contradiction_origins": len(contradicts),
            "support_weight": support_weight, "contradiction_weight": contradiction_weight,
            "strongest_counterargument": counter,
            "falsifier": claim["falsifier"] or "A higher-tier independent record that directly contradicts the claim.",
        }
        if persist and (claim["status"] != status or abs(claim["confidence"] - confidence) > 0.0001):
            stamp = now()
            revision_id = sid("claim-revision", actor["workspace_id"], claim_id, stamp, secrets.token_hex(4))
            with self.store.db() as connection:
                connection.execute("UPDATE atomic_claims SET status=?,confidence=?,updated_at=? WHERE id=? AND workspace_id=?", (status, confidence, stamp, claim_id, actor["workspace_id"]))
                connection.execute("INSERT INTO claim_revisions(id,workspace_id,claim_id,from_status,to_status,from_confidence,to_confidence,reason,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (revision_id, actor["workspace_id"], claim_id, claim["status"], status, claim["confidence"], confidence, str(reason), actor["id"], stamp))
            self.store.identity.audit(actor["workspace_id"], actor["id"], "claim.assessed", "atomic_claim", claim_id, metadata=result)
        return result

    def contradictions(self, actor, state="OPEN", limit=100):
        state = str(state or "OPEN").upper()
        if state not in {"OPEN", "RESOLVED", "ALL"}:
            raise ValueError("invalid contradiction state")
        sql = """SELECT q.*,c.statement,e.title evidence_title,e.url,s.name source_name,s.source_tier
        FROM contradiction_queue q JOIN atomic_claims c ON c.id=q.claim_id
        JOIN claim_evidence e ON e.id=q.evidence_id JOIN source_origins s ON s.id=e.source_origin_id
        WHERE q.workspace_id=?"""
        args = [actor["workspace_id"]]
        if state != "ALL":
            sql += " AND q.state=?"; args.append(state)
        sql += " ORDER BY q.created_at DESC LIMIT ?"; args.append(max(1, min(500, int(limit))))
        with self.store.db() as connection:
            return [dict(row) for row in connection.execute(sql, args).fetchall()]

    def resolve_contradiction(self, actor, contradiction_id, reason="Reviewed by analyst"):
        stamp = now()
        with self.store.db() as connection:
            row = connection.execute("SELECT claim_id FROM contradiction_queue WHERE id=? AND workspace_id=? AND state='OPEN'", (contradiction_id, actor["workspace_id"])).fetchone()
            if not row:
                raise KeyError("open contradiction not found")
            connection.execute("UPDATE contradiction_queue SET state='RESOLVED',reason=?,resolved_at=?,resolved_by=? WHERE id=?", (str(reason), stamp, actor["id"], contradiction_id))
        self.store.identity.audit(actor["workspace_id"], actor["id"], "contradiction.resolved", "contradiction", contradiction_id, metadata={"claim_id": row["claim_id"]})
        return {"id": contradiction_id, "state": "RESOLVED", "resolved_at": stamp, "resolved_by": actor["id"]}

    def lineage(self, actor, claim_id):
        claim = self.claim(actor, claim_id, include_evidence=False)
        records = self.evidence(actor, claim_id)
        nodes = [{"id": claim_id, "type": "claim", "label": claim["statement"], "status": claim["status"]}]
        edges = []
        seen_sources = set()
        for record in records:
            source_node = "source:" + record["source_origin_id"]
            evidence_node = "evidence:" + record["id"]
            if source_node not in seen_sources:
                nodes.append({"id": source_node, "type": "source", "label": record["source_name"], "lineage_key": record["lineage_key"], "tier": record["source_tier"]})
                seen_sources.add(source_node)
            nodes.append({"id": evidence_node, "type": "evidence", "label": record["title"], "url": record["url"], "relation": record["relation"]})
            edges.append({"from": source_node, "to": evidence_node, "type": "PUBLISHED"})
            edges.append({"from": evidence_node, "to": claim_id, "type": record["relation"]})
            if record["duplicate_of"]:
                edges.append({"from": evidence_node, "to": "evidence:" + record["duplicate_of"], "type": "DUPLICATE_OF"})
        return {"claim_id": claim_id, "nodes": nodes, "edges": edges}

    def scorecard(self, actor):
        with self.store.db() as connection:
            status_rows = connection.execute("SELECT status,COUNT(*) total FROM atomic_claims WHERE workspace_id=? GROUP BY status", (actor["workspace_id"],)).fetchall()
            evidence_total = connection.execute("SELECT COUNT(*) total FROM claim_evidence WHERE workspace_id=?", (actor["workspace_id"],)).fetchone()[0]
            duplicate_total = connection.execute("SELECT COUNT(*) total FROM claim_evidence WHERE workspace_id=? AND relation='DUPLICATES'", (actor["workspace_id"],)).fetchone()[0]
            open_contradictions = connection.execute("SELECT COUNT(*) total FROM contradiction_queue WHERE workspace_id=? AND state='OPEN'", (actor["workspace_id"],)).fetchone()[0]
            sources = connection.execute("SELECT COUNT(*) total FROM source_origins WHERE workspace_id=?", (actor["workspace_id"],)).fetchone()[0]
        return {
            "claims_by_status": {row["status"]: int(row["total"]) for row in status_rows},
            "source_origins": int(sources), "evidence_records": int(evidence_total),
            "duplicates_suppressed": int(duplicate_total), "open_contradictions": int(open_contradictions),
        }
