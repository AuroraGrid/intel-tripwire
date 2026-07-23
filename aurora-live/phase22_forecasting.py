from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from phase15_mesh import stable_id

CANDIDATE_STATES = {"PROPOSED", "APPROVED", "SUPPRESSED"}
SUBJECT_TYPES = {"DETECTION", "ROUTE_PLAN"}
ACTION_STATES = {"MONITOR", "PREPARE", "ESCALATE", "HEDGE"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def loads(value: Any, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except (TypeError, json.JSONDecodeError):
        return default


def bounded(value: float, low: float = 0.01, high: float = 0.99) -> float:
    return round(max(low, min(high, float(value))), 4)


class AutonomousForecastEngine:
    """Deterministic, analyst-gated forecast candidate and update engine."""

    SOURCE = "phase22.deterministic"

    def __init__(self, store, forecasts, detection, routes):
        self.store = store
        self.forecasts = forecasts
        self.detection = detection
        self.routes = routes
        self._init_schema()

    def _init_schema(self) -> None:
        with self.store.db() as connection:
            connection.executescript("""
            CREATE TABLE IF NOT EXISTS forecast_base_rates(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, domain TEXT NOT NULL,
                outcome_type TEXT NOT NULL, probability REAL NOT NULL,
                sample_size INTEGER NOT NULL, evidence TEXT NOT NULL,
                created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(workspace_id,domain,outcome_type)
            );
            CREATE TABLE IF NOT EXISTS forecast_candidates(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
                subject_type TEXT NOT NULL, subject_id TEXT NOT NULL,
                question TEXT NOT NULL, description TEXT NOT NULL,
                horizon TEXT NOT NULL, domain TEXT NOT NULL,
                outcome_type TEXT NOT NULL, state TEXT NOT NULL,
                probability REAL NOT NULL, confidence_low REAL NOT NULL,
                confidence_high REAL NOT NULL, base_rate REAL NOT NULL,
                evidence_strength REAL NOT NULL, rationale TEXT NOT NULL,
                trigger_map TEXT NOT NULL, falsifiers TEXT NOT NULL,
                evidence_links TEXT NOT NULL, scenarios TEXT NOT NULL,
                decision_costs TEXT NOT NULL, action_state TEXT NOT NULL,
                forecast_id TEXT, created_by TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(workspace_id,subject_type,subject_id,outcome_type)
            );
            CREATE TABLE IF NOT EXISTS forecast_candidate_revisions(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
                candidate_id TEXT NOT NULL, revision_number INTEGER NOT NULL,
                action TEXT NOT NULL, previous_probability REAL,
                probability REAL NOT NULL, state TEXT NOT NULL,
                rationale TEXT NOT NULL, snapshot TEXT NOT NULL,
                created_by TEXT NOT NULL, created_at TEXT NOT NULL,
                UNIQUE(workspace_id,candidate_id,revision_number)
            );
            CREATE INDEX IF NOT EXISTS idx_forecast_base_rates
                ON forecast_base_rates(workspace_id,domain,outcome_type);
            CREATE INDEX IF NOT EXISTS idx_forecast_candidates_queue
                ON forecast_candidates(workspace_id,state,action_state,probability,updated_at);
            CREATE INDEX IF NOT EXISTS idx_forecast_candidate_subject
                ON forecast_candidates(workspace_id,subject_type,subject_id);
            CREATE INDEX IF NOT EXISTS idx_forecast_candidate_revisions
                ON forecast_candidate_revisions(workspace_id,candidate_id,revision_number);
            """)

    def _workspace(self, actor: dict[str, Any]) -> str:
        return str(actor["workspace_id"])

    def _actor(self, actor: dict[str, Any]) -> str:
        return str(actor.get("id") or "system")

    def set_base_rate(
        self, actor: dict[str, Any], payload: dict[str, Any]
    ) -> dict[str, Any]:
        domain = str(payload.get("domain") or "").strip().lower()
        outcome_type = str(payload.get("outcome_type") or "").strip().upper()
        probability = float(payload.get("probability"))
        sample_size = int(payload.get("sample_size") or 0)
        if not domain or not outcome_type:
            raise ValueError("domain and outcome_type required")
        if not 0 <= probability <= 1:
            raise ValueError("probability must be between 0 and 1")
        if sample_size < 0:
            raise ValueError("sample_size cannot be negative")
        rate_id = stable_id(
            "forecast-base-rate", self._workspace(actor), domain, outcome_type
        )
        stamp = now()
        with self.store.db() as connection:
            connection.execute(
                """INSERT INTO forecast_base_rates(
                id,workspace_id,domain,outcome_type,probability,sample_size,
                evidence,created_by,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(workspace_id,domain,outcome_type) DO UPDATE SET
                probability=excluded.probability,sample_size=excluded.sample_size,
                evidence=excluded.evidence,updated_at=excluded.updated_at""",
                (
                    rate_id, self._workspace(actor), domain, outcome_type,
                    probability, sample_size,
                    json.dumps(payload.get("evidence") or {}, sort_keys=True),
                    self._actor(actor), stamp, stamp,
                ),
            )
        return self.base_rate(actor, domain, outcome_type)

    def base_rate(
        self, actor: dict[str, Any], domain: str, outcome_type: str
    ) -> dict[str, Any]:
        with self.store.db() as connection:
            row = connection.execute(
                "SELECT * FROM forecast_base_rates WHERE workspace_id=? "
                "AND domain=? AND outcome_type=?",
                (
                    self._workspace(actor), str(domain).lower(),
                    str(outcome_type).upper(),
                ),
            ).fetchone()
        if not row:
            return {
                "domain": str(domain).lower(), "outcome_type": str(outcome_type).upper(),
                "probability": 0.25, "sample_size": 0,
                "evidence": {"status": "DEFAULT_UNCALIBRATED"},
            }
        item = dict(row)
        item["probability"] = float(item["probability"])
        item["evidence"] = loads(item["evidence"], {})
        return item

    def _state_factor(self, state: str) -> float:
        return {
            "SUPPORTED": 1.0, "CONFIRMED": 1.0, "PLAUSIBLE": 0.75,
            "NEW": 0.65, "TRIAGED": 0.75, "INVESTIGATING": 0.8,
            "DISPUTED": 0.35, "NOT_PROVEN": 0.3, "RETRACTED": 0.05,
            "REJECTED": 0.05,
        }.get(str(state or "").upper(), 0.55)

    def _decision(
        self, probability: float, costs: dict[str, Any]
    ) -> str:
        miss_cost = max(0.0, float(costs.get("miss_cost") or 1.0))
        false_alarm_cost = max(0.0, float(costs.get("false_alarm_cost") or 1.0))
        expected_miss = probability * miss_cost
        expected_false_alarm = (1 - probability) * false_alarm_cost
        if probability >= 0.75 and expected_miss >= expected_false_alarm:
            return "ESCALATE"
        if probability >= 0.5:
            return "PREPARE"
        if expected_miss > expected_false_alarm * 2:
            return "HEDGE"
        return "MONITOR"

    def _detection_payload(
        self, actor: dict[str, Any], detection_id: str
    ) -> dict[str, Any]:
        item = self.detection.detection(actor, detection_id)
        domain = str(item.get("domain") or "general").lower()
        outcome_type = "MATERIAL_ESCALATION_7D"
        rate = self.base_rate(actor, domain, outcome_type)
        confidence = max(0.0, min(1.0, float(item.get("confidence") or 0) / 100))
        factor = self._state_factor(item.get("effective_state") or item.get("state"))
        independent = max(1, int(item.get("independent_origins") or item.get("independent_sources") or 1))
        corroboration_bonus = min(0.15, (independent - 1) * 0.04)
        contradiction_penalty = min(
            0.25, int(item.get("contradiction_count") or 0) * 0.08
        )
        strength = bounded(confidence * factor + corroboration_bonus - contradiction_penalty, 0, 1)
        probability = bounded(float(rate["probability"]) * 0.35 + strength * 0.65)
        costs = {"miss_cost": 5.0, "false_alarm_cost": 1.0}
        state = str(item.get("effective_state") or item.get("state") or "UNKNOWN")
        return {
            "subject_type": "DETECTION", "subject_id": item["id"],
            "question": f"Will {item['title']} materially escalate or persist within 7 days?",
            "description": (
                "Deterministic candidate derived from a material detection. "
                "It remains unofficial until analyst approval."
            ),
            "horizon": "P7D", "domain": domain, "outcome_type": outcome_type,
            "probability": probability,
            "confidence_low": bounded(probability - 0.2, 0, 1),
            "confidence_high": bounded(probability + 0.2, 0, 1),
            "base_rate": float(rate["probability"]), "evidence_strength": strength,
            "rationale": (
                f"Base rate {float(rate['probability']):.2f}; detection confidence "
                f"{confidence:.2f}; verification state {state}; "
                f"{independent} independent origin(s)."
            ),
            "trigger_map": [
                {"trigger": "A new independent source corroborates the detection",
                 "effect": "INCREASE"},
                {"trigger": "An authoritative source explicitly contradicts the detection",
                 "effect": "DECREASE"},
                {"trigger": "The detection is retracted or rejected", "effect": "RESOLVE_FALSE"},
            ],
            "falsifiers": [
                "The linked detection is retracted or rejected",
                "No material escalation or persistence occurs before the horizon",
            ],
            "evidence_links": [
                {"type": "detection", "id": item["id"]},
                *([{"type": "claim", "id": item["claim_id"]}] if item.get("claim_id") else []),
            ],
            "scenarios": [
                {"name": "Escalation", "probability": probability},
                {"name": "No material escalation", "probability": round(1 - probability, 4)},
            ],
            "decision_costs": costs,
            "action_state": self._decision(probability, costs),
        }

    def _route_payload(
        self, actor: dict[str, Any], plan_id: str
    ) -> dict[str, Any]:
        item = self.routes.plan(actor, plan_id)
        domain = "supply_chain"
        outcome_type = "MATERIAL_ROUTE_DISRUPTION_7D"
        rate = self.base_rate(actor, domain, outcome_type)
        route_risk = max(0.0, min(1.0, float(item["risk_score"]) / 100))
        probability = bounded(float(rate["probability"]) * 0.25 + route_risk * 0.75)
        costs = {"miss_cost": 8.0, "false_alarm_cost": 2.0}
        return {
            "subject_type": "ROUTE_PLAN", "subject_id": item["id"],
            "question": (
                f"Will route {item['name']} require rerouting or suffer a "
                "material disruption within 7 days?"
            ),
            "description": (
                "Deterministic candidate derived from route risk, disruptions, "
                "constraints, and alternatives."
            ),
            "horizon": "P7D", "domain": domain, "outcome_type": outcome_type,
            "probability": probability,
            "confidence_low": bounded(probability - 0.15, 0, 1),
            "confidence_high": bounded(probability + 0.15, 0, 1),
            "base_rate": float(rate["probability"]), "evidence_strength": route_risk,
            "rationale": (
                f"Base rate {float(rate['probability']):.2f}; route risk "
                f"{route_risk:.2f}; binding constraint: {item['binding_constraint']}."
            ),
            "trigger_map": item.get("trigger_map") or [
                {"trigger": "Route risk score crosses 70", "effect": "ESCALATE"}
            ],
            "falsifiers": [
                item.get("falsifier") or "The binding disruption resolves",
                "No rerouting or material delay occurs before the horizon",
            ],
            "evidence_links": [
                {"type": "route_plan", "id": item["id"]},
                *[
                    {"type": "route_disruption", "id": exposure["reference_id"]}
                    for exposure in item.get("exposures") or []
                    if exposure["exposure_type"] == "DISRUPTION"
                ],
            ],
            "scenarios": [
                {"name": "Material disruption", "probability": probability},
                {"name": "Route remains serviceable", "probability": round(1 - probability, 4)},
            ],
            "decision_costs": costs,
            "action_state": self._decision(probability, costs),
        }

    def _candidate_payload(
        self, actor: dict[str, Any], subject_type: str, subject_id: str
    ) -> dict[str, Any]:
        subject_type = str(subject_type).upper()
        if subject_type == "DETECTION":
            return self._detection_payload(actor, subject_id)
        if subject_type == "ROUTE_PLAN":
            return self._route_payload(actor, subject_id)
        raise ValueError("invalid subject_type")

    def propose(
        self, actor: dict[str, Any], subject_type: str, subject_id: str
    ) -> dict[str, Any]:
        payload = self._candidate_payload(actor, subject_type, subject_id)
        candidate_id = stable_id(
            "forecast-candidate", self._workspace(actor),
            payload["subject_type"], payload["subject_id"], payload["outcome_type"],
        )
        stamp = now()
        with self.store.db() as connection:
            existing = connection.execute(
                "SELECT * FROM forecast_candidates WHERE id=? AND workspace_id=?",
                (candidate_id, self._workspace(actor)),
            ).fetchone()
            existing_state = existing["state"] if existing else "PROPOSED"
            forecast_id = existing["forecast_id"] if existing else None
            connection.execute(
                """INSERT INTO forecast_candidates(
                id,workspace_id,subject_type,subject_id,question,description,horizon,
                domain,outcome_type,state,probability,confidence_low,confidence_high,
                base_rate,evidence_strength,rationale,trigger_map,falsifiers,
                evidence_links,scenarios,decision_costs,action_state,forecast_id,
                created_by,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(workspace_id,subject_type,subject_id,outcome_type)
                DO UPDATE SET question=excluded.question,description=excluded.description,
                horizon=excluded.horizon,domain=excluded.domain,
                probability=excluded.probability,confidence_low=excluded.confidence_low,
                confidence_high=excluded.confidence_high,base_rate=excluded.base_rate,
                evidence_strength=excluded.evidence_strength,rationale=excluded.rationale,
                trigger_map=excluded.trigger_map,falsifiers=excluded.falsifiers,
                evidence_links=excluded.evidence_links,scenarios=excluded.scenarios,
                decision_costs=excluded.decision_costs,action_state=excluded.action_state,
                updated_at=excluded.updated_at""",
                (
                    candidate_id, self._workspace(actor), payload["subject_type"],
                    payload["subject_id"], payload["question"], payload["description"],
                    payload["horizon"], payload["domain"], payload["outcome_type"],
                    existing_state, payload["probability"], payload["confidence_low"],
                    payload["confidence_high"], payload["base_rate"],
                    payload["evidence_strength"], payload["rationale"],
                    json.dumps(payload["trigger_map"], sort_keys=True),
                    json.dumps(payload["falsifiers"], sort_keys=True),
                    json.dumps(payload["evidence_links"], sort_keys=True),
                    json.dumps(payload["scenarios"], sort_keys=True),
                    json.dumps(payload["decision_costs"], sort_keys=True),
                    payload["action_state"], forecast_id, self._actor(actor), stamp, stamp,
                ),
            )
        candidate = self.candidate(actor, candidate_id)
        if not existing:
            self._revision(actor, candidate, "PROPOSED", None, "Forecast candidate generated")
        elif candidate["state"] == "APPROVED":
            self._sync_ledger(actor, candidate)
            candidate = self.candidate(actor, candidate_id)
        return candidate

    def _revision(
        self, actor: dict[str, Any], candidate: dict[str, Any],
        action: str, previous_probability: float | None, rationale: str,
    ) -> None:
        with self.store.db() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(revision_number),0)+1 "
                "FROM forecast_candidate_revisions "
                "WHERE workspace_id=? AND candidate_id=?",
                (self._workspace(actor), candidate["id"]),
            ).fetchone()
            number = int(row[0])
            revision_id = stable_id(
                "forecast-candidate-revision", self._workspace(actor),
                candidate["id"], str(number),
            )
            connection.execute(
                """INSERT INTO forecast_candidate_revisions(
                id,workspace_id,candidate_id,revision_number,action,
                previous_probability,probability,state,rationale,snapshot,
                created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    revision_id, self._workspace(actor), candidate["id"], number,
                    action, previous_probability, candidate["probability"],
                    candidate["state"], rationale,
                    json.dumps(candidate, sort_keys=True),
                    self._actor(actor), now(),
                ),
            )

    def approve(
        self, actor: dict[str, Any], candidate_id: str, rationale: str
    ) -> dict[str, Any]:
        candidate = self.candidate(actor, candidate_id)
        if candidate["state"] == "SUPPRESSED":
            raise ValueError("suppressed candidate must be reopened first")
        if not rationale.strip():
            raise ValueError("approval rationale required")
        if not candidate.get("forecast_id"):
            forecast = self.forecasts.create(actor, {
                "question": candidate["question"],
                "description": candidate["description"],
                "horizon": candidate["horizon"],
                "probability": candidate["probability"],
                "confidence_low": candidate["confidence_low"],
                "confidence_high": candidate["confidence_high"],
                "source": self.SOURCE,
                "rationale": f"{candidate['rationale']} Analyst approval: {rationale}",
                "trigger_map": candidate["trigger_map"],
                "falsifiers": candidate["falsifiers"],
                "evidence_links": candidate["evidence_links"],
                "scenarios": candidate["scenarios"],
                "decision_costs": candidate["decision_costs"],
            })
            forecast_id = forecast["id"]
        else:
            forecast_id = candidate["forecast_id"]
        with self.store.db() as connection:
            connection.execute(
                "UPDATE forecast_candidates SET state='APPROVED',forecast_id=?,updated_at=? "
                "WHERE id=? AND workspace_id=?",
                (forecast_id, now(), candidate_id, self._workspace(actor)),
            )
        approved = self.candidate(actor, candidate_id)
        if candidate["state"] != "APPROVED":
            self._revision(
                actor, approved, "APPROVED", candidate["probability"], rationale
            )
        return approved

    def suppress(
        self, actor: dict[str, Any], candidate_id: str, rationale: str
    ) -> dict[str, Any]:
        candidate = self.candidate(actor, candidate_id)
        if not rationale.strip():
            raise ValueError("suppression rationale required")
        with self.store.db() as connection:
            connection.execute(
                "UPDATE forecast_candidates SET state='SUPPRESSED',updated_at=? "
                "WHERE id=? AND workspace_id=?",
                (now(), candidate_id, self._workspace(actor)),
            )
        updated = self.candidate(actor, candidate_id)
        self._revision(
            actor, updated, "SUPPRESSED", candidate["probability"], rationale
        )
        return updated

    def reopen(
        self, actor: dict[str, Any], candidate_id: str, rationale: str
    ) -> dict[str, Any]:
        candidate = self.candidate(actor, candidate_id)
        if candidate["state"] != "SUPPRESSED":
            raise ValueError("candidate is not suppressed")
        if not rationale.strip():
            raise ValueError("reopen rationale required")
        with self.store.db() as connection:
            connection.execute(
                "UPDATE forecast_candidates SET state='PROPOSED',updated_at=? "
                "WHERE id=? AND workspace_id=?",
                (now(), candidate_id, self._workspace(actor)),
            )
        updated = self.candidate(actor, candidate_id)
        self._revision(actor, updated, "REOPENED", candidate["probability"], rationale)
        return updated

    def _sync_ledger(
        self, actor: dict[str, Any], candidate: dict[str, Any]
    ) -> bool:
        forecast_id = candidate.get("forecast_id")
        if not forecast_id:
            return False
        forecast = self.forecasts.get(actor, forecast_id)
        if forecast["status"] != "open":
            return False
        latest = forecast.get("latest_revision") or {}
        previous = float(latest.get("probability") or 0)
        if abs(previous - candidate["probability"]) < 0.02:
            return False
        self.forecasts.revise(actor, forecast_id, {
            "probability": candidate["probability"],
            "confidence_low": candidate["confidence_low"],
            "confidence_high": candidate["confidence_high"],
            "source": self.SOURCE,
            "rationale": candidate["rationale"],
            "trigger_map": candidate["trigger_map"],
            "falsifiers": candidate["falsifiers"],
            "evidence_links": candidate["evidence_links"],
            "scenarios": candidate["scenarios"],
            "decision_costs": candidate["decision_costs"],
        })
        self._revision(
            actor, candidate, "PROBABILITY_UPDATED", previous,
            "Material deterministic evidence update",
        )
        return True

    def candidate(
        self, actor: dict[str, Any], candidate_id: str
    ) -> dict[str, Any]:
        with self.store.db() as connection:
            row = connection.execute(
                "SELECT * FROM forecast_candidates WHERE id=? AND workspace_id=?",
                (candidate_id, self._workspace(actor)),
            ).fetchone()
        if not row:
            raise KeyError("forecast candidate not found")
        item = dict(row)
        for field, default in (
            ("trigger_map", []), ("falsifiers", []), ("evidence_links", []),
            ("scenarios", []), ("decision_costs", {}),
        ):
            item[field] = loads(item[field], default)
        for field in (
            "probability", "confidence_low", "confidence_high",
            "base_rate", "evidence_strength",
        ):
            item[field] = float(item[field])
        return item

    def candidates(
        self, actor: dict[str, Any], state: str = "", limit: int = 100
    ) -> list[dict[str, Any]]:
        sql = "SELECT id FROM forecast_candidates WHERE workspace_id=?"
        args: list[Any] = [self._workspace(actor)]
        if state:
            sql += " AND state=?"
            args.append(str(state).upper())
        sql += " ORDER BY probability DESC,updated_at DESC LIMIT ?"
        args.append(max(1, min(500, int(limit))))
        with self.store.db() as connection:
            rows = connection.execute(sql, args).fetchall()
        return [self.candidate(actor, row["id"]) for row in rows]

    def revisions(
        self, actor: dict[str, Any], candidate_id: str
    ) -> list[dict[str, Any]]:
        self.candidate(actor, candidate_id)
        with self.store.db() as connection:
            rows = connection.execute(
                "SELECT * FROM forecast_candidate_revisions WHERE workspace_id=? "
                "AND candidate_id=? ORDER BY revision_number",
                (self._workspace(actor), candidate_id),
            ).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["snapshot"] = loads(item["snapshot"], {})
            output.append(item)
        return output

    def process(
        self, actor: dict[str, Any], limit: int = 100
    ) -> dict[str, Any]:
        proposed = updated = rejected = 0
        for detection in self.detection.detections(actor, limit=limit):
            try:
                candidate = self.propose(actor, "DETECTION", detection["id"])
                proposed += int(candidate["state"] == "PROPOSED")
                updated += int(candidate["state"] == "APPROVED")
            except (ValueError, KeyError):
                rejected += 1
        for plan in self.routes.plans(actor, "ACTIVE", limit):
            try:
                candidate = self.propose(actor, "ROUTE_PLAN", plan["id"])
                proposed += int(candidate["state"] == "PROPOSED")
                updated += int(candidate["state"] == "APPROVED")
            except (ValueError, KeyError):
                rejected += 1
        return {
            "proposed": proposed, "approved_refreshed": updated, "rejected": rejected
        }

    def scorecard(self, actor: dict[str, Any]) -> dict[str, Any]:
        workspace_id = self._workspace(actor)
        with self.store.db() as connection:
            states = connection.execute(
                "SELECT state,COUNT(*) total FROM forecast_candidates "
                "WHERE workspace_id=? GROUP BY state", (workspace_id,)
            ).fetchall()
            actions = connection.execute(
                "SELECT action_state,COUNT(*) total FROM forecast_candidates "
                "WHERE workspace_id=? GROUP BY action_state", (workspace_id,)
            ).fetchall()
        return {
            "phase": 22,
            "candidates_by_state": {
                row["state"]: int(row["total"]) for row in states
            },
            "candidates_by_action": {
                row["action_state"]: int(row["total"]) for row in actions
            },
            "resolved_forecast_calibration": self.forecasts.metrics(
                actor, self.SOURCE
            ),
            "policy": {
                "analyst_approval_required": True,
                "minimum_material_probability_change": 0.02,
                "external_ai_required": False,
            },
        }
