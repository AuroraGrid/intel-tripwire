from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from phase15_mesh import stable_id


ROOT = Path(__file__).resolve().parent
BASELINE = ROOT / "qualification" / "world_monitor_baseline_2026-07-23.json"

CANONICAL_DOMAIN_MODEL = [
    {
        "record": "Sensor",
        "purpose": "Registered collection capability and health owner",
        "created_by": "administrator or adapter",
        "mutability": "configuration mutable; health append-only",
        "phase": 15,
        "upstream": "external provider",
        "downstream": "Observation",
    },
    {
        "record": "Observation",
        "purpose": "Immutable normalized source record",
        "created_by": "sensor mesh",
        "mutability": "immutable",
        "phase": 15,
        "upstream": "Sensor",
        "downstream": "Detection",
    },
    {
        "record": "Detection",
        "purpose": "Deterministically correlated real-world event candidate",
        "created_by": "correlation engine",
        "mutability": "current state plus append-only revisions",
        "phase": 16,
        "upstream": "Observation",
        "downstream": "Claim and Incident",
    },
    {
        "record": "Claim",
        "purpose": "Atomic fact, inference, forecast, speculation, or unverified assertion",
        "created_by": "system or analyst",
        "mutability": "current assessment plus append-only revisions",
        "phase": 14,
        "upstream": "Detection or analyst",
        "downstream": "Evidence and Incident",
    },
    {
        "record": "Evidence",
        "purpose": "Provenance-backed support or contradiction for one claim",
        "created_by": "system or analyst",
        "mutability": "append-only with corrections",
        "phase": 14,
        "upstream": "Source origin and Observation",
        "downstream": "Claim assessment",
    },
    {
        "record": "Incident",
        "purpose": "Analyst-facing operational package",
        "created_by": "ingestion and analyst workflow",
        "mutability": "versioned operational state",
        "phase": 0,
        "upstream": "Detection and Claim",
        "downstream": "Alert and Case",
    },
    {
        "record": "Alert",
        "purpose": "Rule-triggered notification requiring acknowledgement",
        "created_by": "watchlist evaluation",
        "mutability": "acknowledgement state",
        "phase": 0,
        "upstream": "Incident",
        "downstream": "Analyst action",
    },
    {
        "record": "Case",
        "purpose": "Human investigation workspace",
        "created_by": "analyst",
        "mutability": "collaborative and audited",
        "phase": 0,
        "upstream": "Incident and Alert",
        "downstream": "Decision and Report",
    },
    {
        "record": "Forecast",
        "purpose": "Probabilistic future proposition with resolution",
        "created_by": "analyst-approved candidate",
        "mutability": "append-only probability revisions; immutable resolution",
        "phase": 11,
        "upstream": "Detection and Route Plan",
        "downstream": "Scenario, Action, Calibration",
    },
    {
        "record": "Scenario",
        "purpose": "Conditional future pathway and dependency branch",
        "created_by": "analyst or forecast engine",
        "mutability": "versioned",
        "phase": 11,
        "upstream": "Forecast",
        "downstream": "Decision cost and Action",
    },
    {
        "record": "System Output",
        "purpose": "Stored analytical framework assessment",
        "created_by": "AURORA modules",
        "mutability": "reviewed through append-only adjudication",
        "phase": 11,
        "upstream": "Claim, Incident, Forecast",
        "downstream": "Analyst decision",
    },
    {
        "record": "Audit Event",
        "purpose": "Immutable actor and system action history",
        "created_by": "all material workflows",
        "mutability": "immutable",
        "phase": 4,
        "upstream": "material action",
        "downstream": "qualification and accountability",
    },
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class IntegrationQualifier:
    """Reproducible integration probes and dated competitive benchmark."""

    def __init__(self, store, services: dict[str, Any]):
        self.store = store
        self.services = services
        self.baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        self._init_schema()

    def _init_schema(self) -> None:
        with self.store.db() as connection:
            connection.executescript("""
            CREATE TABLE IF NOT EXISTS qualification_runs(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
                phase INTEGER NOT NULL, status TEXT NOT NULL,
                checks_passed INTEGER NOT NULL, checks_failed INTEGER NOT NULL,
                checks_not_verified INTEGER NOT NULL, report TEXT NOT NULL,
                created_by TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS qualification_checks(
                id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
                run_id TEXT NOT NULL, name TEXT NOT NULL,
                status TEXT NOT NULL, required INTEGER NOT NULL,
                evidence TEXT NOT NULL, created_at TEXT NOT NULL,
                UNIQUE(workspace_id,run_id,name)
            );
            CREATE INDEX IF NOT EXISTS idx_qualification_runs
                ON qualification_runs(workspace_id,created_at);
            CREATE INDEX IF NOT EXISTS idx_qualification_checks
                ON qualification_checks(workspace_id,run_id,status);
            """)

    def _workspace(self, actor: dict[str, Any]) -> str:
        return str(actor["workspace_id"])

    def _actor(self, actor: dict[str, Any]) -> str:
        return str(actor.get("id") or "system")

    @staticmethod
    def _probe(
        name: str, callback: Callable[[], Any], required: bool = True
    ) -> dict[str, Any]:
        try:
            evidence = callback()
            return {
                "name": name,
                "status": "PASS",
                "required": required,
                "evidence": evidence,
            }
        except Exception as exc:
            return {
                "name": name,
                "status": "FAIL" if required else "NOT_VERIFIED",
                "required": required,
                "evidence": {
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                },
            }

    def domain_model(self) -> dict[str, Any]:
        return {
            "version": "25.0.0",
            "records": CANONICAL_DOMAIN_MODEL,
            "rules": [
                "Observations remain immutable source records.",
                "Detections correlate observations but do not replace them.",
                "Claims are atomic assertions and evidence attaches to claims.",
                "Incidents package intelligence for operations and cases.",
                "Forecasts remain separate from verified outcomes.",
                "All workspace-scoped records preserve tenant boundaries.",
                "Material state changes require immutable revisions or audit events.",
            ],
        }

    def _api_operations(self) -> int:
        spec = self.services["developer"].openapi()
        return sum(
            method.lower() in {"get", "post", "put", "patch", "delete"}
            for operations in spec["paths"].values()
            for method in operations
        )

    def benchmark(self, actor: dict[str, Any]) -> dict[str, Any]:
        sensors = self.services["mesh"].sensors(actor)
        layers = self.services["delivery"].layers(actor)
        operations = self._api_operations()
        tools = len(self.services["mcp"].tool_definitions())
        forecast_metrics = self.services["forecasts"].metrics(actor)
        resolved = int(
            forecast_metrics.get("count")
            or forecast_metrics.get("total")
            or forecast_metrics.get("forecasts")
            or 0
        )
        wm = self.baseline["capabilities"]
        matrix = [
            {
                "capability": "Live monitoring breadth",
                "aurora": {
                    "registered_sensors": len(sensors),
                    "live_qualified": self.services["mesh"].coverage(actor),
                },
                "world_monitor": {
                    "external_sources": wm["external_data_sources"]["value"],
                    "curated_feeds": wm["curated_news_feeds"]["value"],
                },
                "result": "NOT_VERIFIED",
                "reason": "Registry entries and external live sources are not equivalent; sustained live qualification is required.",
            },
            {
                "capability": "Configured map layers",
                "aurora": len(layers),
                "world_monitor": wm["map_layers"]["value"],
                "result": (
                    "PARITY"
                    if len(layers) == wm["map_layers"]["value"]
                    else "AHEAD"
                    if len(layers) > wm["map_layers"]["value"]
                    else "BEHIND"
                ),
                "reason": "Direct configured-layer count; layer usefulness and performance remain separate gates.",
            },
            {
                "capability": "Documented API operations",
                "aurora": operations,
                "world_monitor": wm["openapi_operations"]["value"],
                "result": (
                    "PARITY"
                    if operations == wm["openapi_operations"]["value"]
                    else "AHEAD"
                    if operations > wm["openapi_operations"]["value"]
                    else "BEHIND"
                ),
                "reason": "OpenAPI operation count is measurable but does not measure quality.",
            },
            {
                "capability": "MCP tools",
                "aurora": tools,
                "world_monitor": "documented, count not verified",
                "result": "NOT_VERIFIED",
                "reason": "World Monitor public documentation confirms MCP but not a stable comparable tool count.",
            },
            {
                "capability": "Claim-level provenance and contradiction ledger",
                "aurora": "implemented and regression-tested",
                "world_monitor": "source correlation and deduplication documented",
                "result": "AHEAD",
                "reason": "AURORA exposes atomic claims, origin lineage, contradictions, falsifiers, and append-only correction history.",
            },
            {
                "capability": "Resolved forecast calibration",
                "aurora": {
                    "resolved_forecasts": resolved,
                    "metrics": forecast_metrics,
                },
                "world_monitor": "no comparable public resolved-forecast scorecard verified",
                "result": "AHEAD" if resolved >= 30 else "NOT_VERIFIED",
                "reason": "Architecture exists, but a meaningful live track record requires at least 30 resolved forecasts.",
            },
            {
                "capability": "Route and scenario usability",
                "aurora": "implemented route constraints, alternatives, forecasts, and actions",
                "world_monitor": wm["workflows"]["value"],
                "result": "NOT_VERIFIED",
                "reason": "A direct timed usability and scenario-output benchmark has not yet run.",
            },
            {
                "capability": "Public uptime and battle testing",
                "aurora": "qualification pending",
                "world_monitor": "public operating product",
                "result": "BEHIND",
                "reason": "AURORA lacks equivalent sustained public operating history.",
            },
        ]
        strategic = [row for row in matrix if row["result"] == "BEHIND"]
        unknown = [row for row in matrix if row["result"] == "NOT_VERIFIED"]
        return {
            "benchmark_date": self.baseline["verified_at"],
            "world_monitor_baseline": self.baseline,
            "matrix": matrix,
            "summary": {
                "ahead": sum(row["result"] == "AHEAD" for row in matrix),
                "parity": sum(row["result"] == "PARITY" for row in matrix),
                "behind": len(strategic),
                "not_verified": len(unknown),
                "superiority_claim_allowed": not strategic and not unknown,
            },
        }

    def run(self, actor: dict[str, Any]) -> dict[str, Any]:
        s = self.services
        checks = [
            self._probe(
                "database",
                lambda: {
                    "backend": self.store.backend,
                    "workspace_id": self._workspace(actor),
                },
            ),
            self._probe("sensor_mesh", lambda: s["mesh"].coverage(actor)),
            self._probe(
                "evidence_integrity", lambda: s["integrity"].scorecard(actor)
            ),
            self._probe(
                "detection_correlation", lambda: s["detection"].scorecard(actor)
            ),
            self._probe("event_fabric", lambda: s["fabric"].status(actor)),
            self._probe("entity_graph", lambda: s["graph"].scorecard(actor)),
            self._probe(
                "multimodal_verification", lambda: s["media"].scorecard(actor)
            ),
            self._probe(
                "operating_picture", lambda: s["picture"].scorecard(actor)
            ),
            self._probe("route_intelligence", lambda: s["routes"].scorecard(actor)),
            self._probe(
                "autonomous_forecasts",
                lambda: s["autonomous"].scorecard(actor),
            ),
            self._probe(
                "unified_experience", lambda: s["command"].overview(actor)
            ),
            self._probe(
                "developer_ecosystem", lambda: s["developer"].scorecard(actor)
            ),
            {
                "name": "sustained_public_uptime",
                "status": "NOT_VERIFIED",
                "required": False,
                "evidence": {
                    "requirement": "Phase 26 production observation window",
                    "minimum_target": "99.9% monthly uptime",
                },
            },
            {
                "name": "independent_security_review",
                "status": "NOT_VERIFIED",
                "required": False,
                "evidence": {
                    "requirement": "third-party review",
                    "cannot_be_self_certified": True,
                },
            },
        ]
        benchmark = self.benchmark(actor)
        passed = sum(check["status"] == "PASS" for check in checks)
        failed = sum(check["status"] == "FAIL" for check in checks)
        unknown = sum(check["status"] == "NOT_VERIFIED" for check in checks)
        status = "PASS" if failed == 0 else "FAIL"
        stamp = now()
        run_id = stable_id(
            "qualification-run",
            self._workspace(actor),
            stamp,
            json.dumps(checks, sort_keys=True, default=str),
        )
        report = {
            "phase": 25,
            "run_id": run_id,
            "status": status,
            "created_at": stamp,
            "checks": checks,
            "domain_model": self.domain_model(),
            "benchmark": benchmark,
            "release_gate": {
                "integration_checks_pass": failed == 0,
                "external_gates_pending": unknown,
                "competitive_superiority_proven": benchmark["summary"][
                    "superiority_claim_allowed"
                ],
            },
        }
        with self.store.db() as connection:
            connection.execute(
                """INSERT INTO qualification_runs(
                id,workspace_id,phase,status,checks_passed,checks_failed,
                checks_not_verified,report,created_by,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id,
                    self._workspace(actor),
                    25,
                    status,
                    passed,
                    failed,
                    unknown,
                    json.dumps(report, sort_keys=True, default=str),
                    self._actor(actor),
                    stamp,
                ),
            )
            for check in checks:
                check_id = stable_id(
                    "qualification-check",
                    self._workspace(actor),
                    run_id,
                    check["name"],
                )
                connection.execute(
                    """INSERT INTO qualification_checks(
                    id,workspace_id,run_id,name,status,required,evidence,created_at)
                    VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        check_id,
                        self._workspace(actor),
                        run_id,
                        check["name"],
                        check["status"],
                        int(check["required"]),
                        json.dumps(
                            check["evidence"], sort_keys=True, default=str
                        ),
                        stamp,
                    ),
                )
        self.store.identity.audit(
            self._workspace(actor),
            self._actor(actor),
            "qualification.completed",
            "qualification_run",
            run_id,
            metadata={
                "status": status,
                "passed": passed,
                "failed": failed,
                "not_verified": unknown,
            },
        )
        return report

    def latest(self, actor: dict[str, Any]) -> dict[str, Any]:
        with self.store.db() as connection:
            row = connection.execute(
                """SELECT report FROM qualification_runs WHERE workspace_id=?
                ORDER BY created_at DESC LIMIT 1""",
                (self._workspace(actor),),
            ).fetchone()
        if not row:
            return {
                "phase": 25,
                "status": "NOT_RUN",
                "domain_model": self.domain_model(),
                "benchmark": self.benchmark(actor),
            }
        return json.loads(row["report"])

    def runs(
        self, actor: dict[str, Any], limit: int = 50
    ) -> list[dict[str, Any]]:
        with self.store.db() as connection:
            rows = connection.execute(
                """SELECT id,phase,status,checks_passed,checks_failed,
                checks_not_verified,created_by,created_at
                FROM qualification_runs WHERE workspace_id=?
                ORDER BY created_at DESC LIMIT ?""",
                (self._workspace(actor), max(1, min(200, int(limit)))),
            ).fetchall()
        return [dict(row) for row in rows]
