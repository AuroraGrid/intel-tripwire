from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta
from typing import Any

from phase26_operations import (
    _format_timestamp,
    _parse_timestamp,
    _reject_future_timestamp,
)
from storage import now, sid


RESULTS = {"AHEAD", "PARITY", "BEHIND", "NOT_VERIFIED"}
EVIDENCE_REFERENCE_KEYS = {
    "artifact",
    "checksum",
    "report",
    "report_url",
    "run_id",
    "sha256",
    "url",
}

GAP_CATALOG = {
    "live-monitoring-breadth": {
        "capability": "Live monitoring breadth",
        "strategic": True,
        "max_age_days": 7,
        "evidence_class": "LIVE_QUALIFICATION",
        "criteria": {
            "minimum_external_sources": 65,
            "minimum_curated_feeds": 500,
            "minimum_window_hours": 24,
            "minimum_success_percent": 95.0,
        },
    },
    "configured-map-layers": {
        "capability": "Configured map layers",
        "strategic": True,
        "max_age_days": 30,
        "evidence_class": "AUTOMATED_CONFORMANCE",
        "criteria": {"compare_to_current_baseline": True},
    },
    "documented-api-operations": {
        "capability": "Documented API operations",
        "strategic": False,
        "max_age_days": 30,
        "evidence_class": "AUTOMATED_CONFORMANCE",
        "criteria": {"compare_to_current_baseline": True},
    },
    "mcp-tools": {
        "capability": "MCP tools",
        "strategic": False,
        "max_age_days": 30,
        "evidence_class": "AUTOMATED_CONFORMANCE",
        "criteria": {
            "conformance_required": True,
            "competitor_target_must_be_verified": True,
        },
    },
    "resolved-forecast-calibration": {
        "capability": "Resolved forecast calibration",
        "strategic": True,
        "max_age_days": 30,
        "evidence_class": "OUTCOME_HISTORY",
        "criteria": {
            "minimum_resolved_forecasts": 30,
            "calibration_metrics_required": True,
        },
    },
    "route-and-scenario-usability": {
        "capability": "Route and scenario usability",
        "strategic": True,
        "max_age_days": 90,
        "evidence_class": "TIMED_USABILITY",
        "criteria": {
            "minimum_tasks": 10,
            "minimum_participants": 3,
            "minimum_success_percent": 90.0,
            "independent_required": True,
        },
    },
    "public-uptime-and-battle-testing": {
        "capability": "Public uptime and battle testing",
        "strategic": True,
        "max_age_days": 7,
        "evidence_class": "EXTERNAL_OPERATIONS",
        "criteria": {
            "minimum_uptime_percent": 99.9,
            "minimum_window_days": 30,
            "independent_required": True,
        },
    },
}


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _finite_number(value: Any, field: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{field} must be a finite non-negative number")
    return result


class CompetitiveGapClosure:
    """Append-only evidence ledger for post-qualification gap closure."""

    def __init__(self, store, qualifier):
        self.store = store
        self.qualifier = qualifier
        self.init()

    def init(self) -> None:
        with self.store.db() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS competitive_gaps(
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    slug TEXT NOT NULL,
                    capability TEXT NOT NULL,
                    strategic INTEGER NOT NULL,
                    baseline_result TEXT NOT NULL,
                    baseline_reason TEXT NOT NULL,
                    evidence_class TEXT NOT NULL,
                    criteria TEXT NOT NULL,
                    benchmark TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(workspace_id,slug)
                );
                CREATE TABLE IF NOT EXISTS competitive_evidence(
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    gap_id TEXT NOT NULL,
                    result TEXT NOT NULL,
                    source TEXT NOT NULL,
                    independent INTEGER NOT NULL,
                    metrics TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    actor_user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_competitive_gaps_workspace
                    ON competitive_gaps(workspace_id,strategic,slug);
                CREATE INDEX IF NOT EXISTS idx_competitive_evidence_gap
                    ON competitive_evidence(
                        workspace_id,gap_id,observed_at DESC,created_at DESC
                    );
                """
            )

    @staticmethod
    def _workspace(actor: dict[str, Any]) -> str:
        return str(actor["workspace_id"])

    @staticmethod
    def _actor(actor: dict[str, Any]) -> str:
        return str(actor["id"])

    def _benchmark_rows(
        self, actor: dict[str, Any]
    ) -> dict[str, dict[str, Any]]:
        return {
            _slug(row["capability"]): row
            for row in self.qualifier.benchmark(actor)["matrix"]
        }

    def sync(self, actor: dict[str, Any]) -> dict[str, Any]:
        self.store.identity.require(actor, "admin")
        rows = self._benchmark_rows(actor)
        stamp = now()
        workspace = self._workspace(actor)
        synchronized = 0
        with self.store.db() as connection:
            for slug, row in rows.items():
                catalog = GAP_CATALOG.get(slug)
                if not catalog:
                    catalog = {
                        "capability": row["capability"],
                        "strategic": row["result"] == "BEHIND",
                        "max_age_days": 30,
                        "evidence_class": "MANUAL_REVIEW",
                        "criteria": {},
                    }
                gap_id = sid("competitive-gap", workspace, slug)
                connection.execute(
                    """
                    INSERT INTO competitive_gaps(
                        id,workspace_id,slug,capability,strategic,
                        baseline_result,baseline_reason,evidence_class,
                        criteria,benchmark,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(workspace_id,slug) DO UPDATE SET
                        capability=excluded.capability,
                        strategic=excluded.strategic,
                        baseline_result=excluded.baseline_result,
                        baseline_reason=excluded.baseline_reason,
                        evidence_class=excluded.evidence_class,
                        criteria=excluded.criteria,
                        benchmark=excluded.benchmark,
                        updated_at=excluded.updated_at
                    """,
                    (
                        gap_id,
                        workspace,
                        slug,
                        row["capability"],
                        int(catalog["strategic"]),
                        row["result"],
                        row["reason"],
                        catalog["evidence_class"],
                        json.dumps(catalog["criteria"], sort_keys=True),
                        json.dumps(row, sort_keys=True, default=str),
                        stamp,
                        stamp,
                    ),
                )
                synchronized += 1
        self.store.identity.audit(
            workspace,
            self._actor(actor),
            "competitive_gaps.synchronized",
            "competitive_gap_set",
            workspace,
            metadata={"rows": synchronized},
        )
        return self.summary(actor)

    def _rows(self, actor: dict[str, Any]) -> list[dict[str, Any]]:
        with self.store.db() as connection:
            rows = connection.execute(
                """
                SELECT * FROM competitive_gaps
                WHERE workspace_id=? ORDER BY strategic DESC,slug
                """,
                (self._workspace(actor),),
            ).fetchall()
        return [dict(row) for row in rows]

    def _gap(self, actor: dict[str, Any], slug: str) -> dict[str, Any]:
        normalized = _slug(slug)
        with self.store.db() as connection:
            row = connection.execute(
                """
                SELECT * FROM competitive_gaps
                WHERE workspace_id=? AND slug=?
                """,
                (self._workspace(actor), normalized),
            ).fetchone()
        if not row:
            raise KeyError("competitive gap not found")
        return dict(row)

    def _latest_evidence(
        self, actor: dict[str, Any], gap_id: str
    ) -> dict[str, Any] | None:
        with self.store.db() as connection:
            row = connection.execute(
                """
                SELECT * FROM competitive_evidence
                WHERE workspace_id=? AND gap_id=?
                ORDER BY observed_at DESC,created_at DESC LIMIT 1
                """,
                (self._workspace(actor), gap_id),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["metrics"] = json.loads(item["metrics"])
        item["evidence"] = json.loads(item["evidence"])
        item["independent"] = bool(item["independent"])
        return item

    @staticmethod
    def _criteria_result(
        gap: dict[str, Any],
        evidence: dict[str, Any] | None,
        as_of: datetime,
    ) -> tuple[str, str]:
        if not evidence:
            baseline = gap["baseline_result"]
            if baseline in {"AHEAD", "PARITY"}:
                return (
                    "NOT_VERIFIED",
                    "fresh Phase 27 evidence is required to close the gap",
                )
            return baseline, "no newer evidence recorded"
        if _parse_timestamp(evidence["expires_at"]) < as_of:
            return "NOT_VERIFIED", "latest evidence expired"
        if evidence["result"] in {"BEHIND", "NOT_VERIFIED"}:
            return evidence["result"], "latest evidence does not close the gap"

        criteria = json.loads(gap["criteria"])
        metrics = evidence["metrics"]
        if criteria.get("independent_required") and not evidence["independent"]:
            return "NOT_VERIFIED", "independent evidence is required"
        if criteria.get("competitor_target_must_be_verified"):
            if not metrics.get("competitor_target_verified"):
                return "NOT_VERIFIED", "competitor target is not verified"
        comparisons = (
            ("minimum_external_sources", "external_sources"),
            ("minimum_curated_feeds", "curated_feeds"),
            ("minimum_window_hours", "window_hours"),
            ("minimum_success_percent", "success_percent"),
            ("minimum_resolved_forecasts", "resolved_forecasts"),
            ("minimum_tasks", "tasks"),
            ("minimum_participants", "participants"),
            ("minimum_uptime_percent", "uptime_percent"),
            ("minimum_window_days", "window_days"),
        )
        for criterion, metric in comparisons:
            if criterion in criteria and float(metrics.get(metric, -1)) < float(
                criteria[criterion]
            ):
                return "NOT_VERIFIED", f"{metric} does not meet the threshold"
        if criteria.get("calibration_metrics_required") and not all(
            key in metrics for key in ("brier_score", "log_loss")
        ):
            return "NOT_VERIFIED", "calibration metrics are incomplete"
        if criteria.get("conformance_required") and not metrics.get(
            "conformance_passed"
        ):
            return "NOT_VERIFIED", "conformance did not pass"
        if criteria.get("compare_to_current_baseline"):
            if float(metrics.get("aurora", -1)) < float(
                metrics.get("world_monitor", math.inf)
            ):
                return "BEHIND", "measured capability remains below baseline"
        return evidence["result"], "latest evidence meets explicit criteria"

    def gaps(
        self, actor: dict[str, Any], as_of: str | None = None
    ) -> list[dict[str, Any]]:
        reference = _parse_timestamp(str(as_of or now()))
        result = []
        for row in self._rows(actor):
            evidence = self._latest_evidence(actor, row["id"])
            status, reason = self._criteria_result(row, evidence, reference)
            row["strategic"] = bool(row["strategic"])
            row["criteria"] = json.loads(row["criteria"])
            row["benchmark"] = json.loads(row["benchmark"])
            row["latest_evidence"] = evidence
            row["current_result"] = status
            row["current_reason"] = reason
            row["closed"] = status in {"AHEAD", "PARITY"}
            result.append(row)
        return result

    def summary(
        self, actor: dict[str, Any], as_of: str | None = None
    ) -> dict[str, Any]:
        rows = self.gaps(actor, as_of)
        counts = {
            result: sum(row["current_result"] == result for row in rows)
            for result in sorted(RESULTS)
        }
        strategic_open = [
            row["slug"]
            for row in rows
            if row["strategic"] and not row["closed"]
        ]
        return {
            "phase": 27,
            "gaps": rows,
            "counts": counts,
            "strategic_open": strategic_open,
            "strategic_gaps_closed": not strategic_open,
            "superiority_claim_candidate": bool(rows) and not strategic_open,
            "policy": {
                "latest_evidence_wins": True,
                "evidence_expires": True,
                "failures_override_old_passes": True,
                "external_claims_cannot_be_self_certified": True,
                "feature_counts_do_not_prove_usability": True,
            },
        }

    def record_evidence(
        self, actor: dict[str, Any], payload: dict[str, Any]
    ) -> dict[str, Any]:
        self.store.identity.require(actor, "admin")
        gap = self._gap(actor, str(payload.get("capability") or ""))
        result = str(payload.get("result") or "").strip().upper()
        if result not in RESULTS:
            raise ValueError("invalid competitive result")
        source = str(payload.get("source") or "").strip()
        if not source:
            raise ValueError("source is required")
        metrics = payload.get("metrics") or {}
        evidence = payload.get("evidence") or {}
        if not isinstance(metrics, dict) or not isinstance(evidence, dict):
            raise ValueError("metrics and evidence must be objects")
        if not any(
            evidence.get(key) not in (None, "", [], {})
            for key in EVIDENCE_REFERENCE_KEYS
        ):
            raise ValueError("evidence requires a verifiable reference")
        for key, value in metrics.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                _finite_number(value, f"metrics.{key}")

        independent = bool(payload.get("independent", False))
        criteria = json.loads(gap["criteria"])
        if (
            result in {"AHEAD", "PARITY"}
            and criteria.get("independent_required")
            and not independent
        ):
            raise ValueError("independent evidence is required for closure")

        observed = _parse_timestamp(str(payload.get("observed_at") or now()))
        _reject_future_timestamp(observed, "observed_at")
        max_age_days = int(
            GAP_CATALOG.get(gap["slug"], {}).get("max_age_days", 30)
        )
        expires = observed + timedelta(days=max_age_days)
        observed_text = _format_timestamp(observed)
        expires_text = _format_timestamp(expires)
        workspace = self._workspace(actor)
        evidence_id = sid(
            "competitive-evidence",
            workspace,
            gap["id"],
            observed_text,
            result,
            json.dumps(evidence, sort_keys=True),
        )
        stamp = now()
        with self.store.db() as connection:
            connection.execute(
                """
                INSERT INTO competitive_evidence(
                    id,workspace_id,gap_id,result,source,independent,
                    metrics,evidence,observed_at,expires_at,actor_user_id,
                    created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO NOTHING
                """,
                (
                    evidence_id,
                    workspace,
                    gap["id"],
                    result,
                    source,
                    int(independent),
                    json.dumps(metrics, sort_keys=True),
                    json.dumps(evidence, sort_keys=True),
                    observed_text,
                    expires_text,
                    self._actor(actor),
                    stamp,
                ),
            )
        self.store.identity.audit(
            workspace,
            self._actor(actor),
            "competitive_evidence.recorded",
            "competitive_evidence",
            evidence_id,
            metadata={
                "capability": gap["slug"],
                "result": result,
                "independent": independent,
            },
        )
        return self.evidence(actor, gap["slug"], limit=1)[0]

    def evidence(
        self, actor: dict[str, Any], capability: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        gap = self._gap(actor, capability)
        limit = max(1, min(500, int(limit)))
        with self.store.db() as connection:
            rows = connection.execute(
                """
                SELECT * FROM competitive_evidence
                WHERE workspace_id=? AND gap_id=?
                ORDER BY observed_at DESC,created_at DESC LIMIT ?
                """,
                (self._workspace(actor), gap["id"], limit),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["metrics"] = json.loads(item["metrics"])
            item["evidence"] = json.loads(item["evidence"])
            item["independent"] = bool(item["independent"])
            result.append(item)
        return result
