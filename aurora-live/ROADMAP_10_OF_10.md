# AURORA 10/10 World Intelligence Program

AURORA's target is not feature parity with World Monitor. The target is to exceed it as a decision-intelligence system while matching or surpassing its live global-monitoring breadth, speed, geographic experience, transparency, and reliability.

AURORA is not called 10/10 because a roadmap says so. The designation is earned only after every release gate is reproducibly verified and the public benchmark shows a clear overall win.

## Competitive baseline

World Monitor publicly reports 56 live map layers, 500+ curated feeds, 65+ named providers, 13 maritime chokepoints, 86 submarine cables, 88 pipelines and LNG terminals, 313 AI datacenters, 29 geopolitical hotspots, 92 market assets, 196 country resilience rankings, 39 MCP tools, 193 REST operations, 154 command-palette actions, 24 interface languages, and five independent origin types behind breaking alerts.

Sources:
- https://www.worldmonitor.app/
- https://www.wired.com/story/world-monitor-elie-habib/

These numbers are a moving target and must be rechecked before every major AURORA release.

## Product doctrine

- Evidence first; no certainty theater.
- Separate FACT, INFERENCE, FORECAST, and SPECULATION.
- Prefer primary records and preserve source lineage.
- Count corroboration only after syndication and duplicate collapse.
- Every high-impact assessment includes the strongest counterargument and a falsifier.
- Every serious incident resolves to an AURORA action state.
- AAIK acts as a circuit breaker during spike conditions.
- Human analysts can inspect, challenge, override, and audit automated conclusions.
- Every score must expose its inputs, timestamps, provenance, and uncertainty.

## Phase 8: operational depth

Implemented in PR #19:

- Live source and worker health telemetry.
- Automatic polling, stale-data warnings, and explicit refresh state.
- Incident timelines and evidence graphs.
- Direct incident-to-case attachment and analyst notes.
- Source reliability, lineage, freshness, and duplicate-suppression indicators.
- Regression tests for the Phase 8 runtime and API contracts.
- Live qualification tooling for deployed and runtime-snapshot verification.

Phase 8 is complete only when automated CI, live upstream qualification, and a local deployment qualification all pass.

## Phase 9: data breadth

- Exceed 65 named providers and 500 curated feeds.
- Deliver at least 60 independently useful live map/data layers across conflict, security, maritime, aviation, space, energy, infrastructure, markets, macroeconomics, cyber, connectivity, climate, hazards, humanitarian response, public health, and governance.
- Separate direct sensor/official observations from news-derived claims.
- Add per-source caching, rate limiting, retries, exponential backoff, health scoring, licensing metadata, and degraded-mode behavior.
- Build a source registry with owner, capability, refresh interval, provenance, reliability history, geographic coverage, and retention policy.

## Phase 10: geographic intelligence

- Replace the static SVG map with a high-performance interactive WebGL geographic engine.
- Support clustering, zoom, time windows, region filters, bounding-box queries, heat maps, trails, layer controls, and thousands of moving objects without frame drops.
- Exceed the public World Monitor infrastructure baseline: more than 13 chokepoints, 86 cables, 88 pipeline/LNG assets, 313 AI datacenters, 29 hotspots, and 92 tracked market assets.
- Add country and regional dossiers, dependencies, routes, exposure graphs, and resilience summaries for at least 196 countries.

## Phase 11: forecasting and decision intelligence

This is the principal area where AURORA must clearly outperform World Monitor.

- Persist forecasts, probabilities, confidence intervals, trigger maps, falsifiers, outcomes, and calibration scores.
- Add Forecast Portfolio and Hall of Record views.
- Back AURORA GRID, K-ALIGN, CRF, IPR, BLACKGLASS, COMMAND, and AAIK outputs with stored computation rather than labels alone.
- Add red-team review, analyst adjudication, immutable audit history, and model-versus-human comparison.
- Add scenario branching, route-risk analysis, dependency propagation, and explicit decision-cost modeling.
- Measure Brier score, log loss, calibration error, alert precision, alert recall, false-alarm rate, detection latency, and time-to-decision.

## Phase 12: platform and agent ecosystem

- Exceed 39 MCP tools and 193 REST operations with stable schemas, SDKs, projections, rate limits, and audit logs.
- Add at least 160 command-palette actions and 25 interface languages with right-to-left support.
- Support cloud, on-premises, and air-gapped deployment.
- Add team workspaces, SSO, MFA, RBAC, signed exports, retention controls, and policy enforcement.
- Provide accessible desktop, mobile, television, and wall-display experiences.

## Phase 13: production quality

- Public HTTPS deployment with backups, observability, source SLOs, disaster recovery, accessibility, caching, pagination, and exports.
- Load, failure, chaos, recovery, and security testing under concurrent collection and analyst use.
- Document deployment, rollback, incident response, data retention, licensing, privacy, and threat models.
- Publish uptime, source freshness, detection latency, alert quality, and forecast calibration metrics.

## 10/10 release gate

AURORA is not labeled 10/10 until all of the following are true:

1. More than 65 reliable named providers and more than 500 curated feeds.
2. At least 60 useful live layers across 15 or more capability classes.
3. Interactive WebGL mapping with moving-object support and sub-second local filtering.
4. Geographic and infrastructure coverage that exceeds the current World Monitor public baseline.
5. Provenance, source health, lineage, licensing, reliability, and freshness visible for every incident.
6. Working alert, case, forecast, audit, evidence, red-team, scenario, and decision-routing workflows.
7. Forecasting is measured against resolved outcomes and publishes calibration metrics.
8. No unlabeled fixture, simulated, stale, or demonstration data in live mode.
9. Automated tests across Python 3.10-3.13, PostgreSQL, containers, API, dashboard, delivery, recovery, security, and live-source paths.
10. Public deployment, backup restore, rollback, and disaster-recovery exercises pass.
11. Accessibility, localization, mobile, desktop, and wall-display acceptance tests pass.
12. A published, reproducible capability matrix shows AURORA matching or exceeding World Monitor on monitoring breadth and clearly exceeding it on evidence auditability, forecasting calibration, analyst workflow, and decision routing.
13. An independent reviewer can reproduce the qualification report without private developer knowledge.

## Verification policy

Every gate is `VERIFIED`, `PARTIAL`, or `NOT VERIFIED`. Claims are supported by CI run IDs, qualification reports, benchmark artifacts, and deployment evidence. A passing unit test cannot substitute for live-source validation; a live-source check cannot substitute for recovery or security testing; and a feature count cannot substitute for demonstrated analyst utility.

## Current status

- Phase 8 code and automated tests: VERIFIED.
- Phase 8 GitHub-hosted container, SQLite, PostgreSQL, API, dashboard, worker, recovery, and load paths: VERIFIED.
- Phase 8 live upstream-source qualification: pending the new qualification workflow.
- Local Docker deployment using the merged Phase 8 branch: NOT VERIFIED remotely because the local machine is not accessible from GitHub CI.
- 10/10 designation: NOT VERIFIED. Phases 9-13 remain required.
