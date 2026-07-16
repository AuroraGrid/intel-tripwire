# AURORA 9/10 Product Program

AURORA will not reach a credible 9/10 by changing the dashboard alone. The release gate requires comparable live-data breadth to major global-monitoring products plus stronger evidence discipline, forecasting, auditability, and decision routing.

## Product principles

- Evidence first; no certainty theater.
- Separate FACT, INFERENCE, FORECAST, and SPECULATION.
- Prefer primary records and preserve source lineage.
- Treat corroboration as independent only after syndication and duplicate collapse.
- Every high-impact assessment includes a counterargument and falsifier.
- Every serious incident resolves to an AURORA action state.
- AAIK acts as a circuit breaker during spike conditions.

## Phase 8: operational depth

- Show live source and worker health in the console.
- Add automatic polling, stale-data warnings, and explicit refresh state.
- Surface incident timelines and evidence graphs.
- Attach incidents to cases and add notes directly from incident detail.
- Display source reliability, lineage, freshness, and duplicates suppressed.
- Add regression tests for dashboard and API contracts.

## Phase 9: data breadth

- Expand to at least 25 reliable live adapters across eight or more capability classes.
- Target weather alerts, humanitarian reporting, cyber advisories, seismic events, space weather, transport and infrastructure, energy, public health, and geopolitical reporting.
- Separate direct observations from news-derived claims.
- Add per-source caching, rate limiting, retries, backoff, health scores, and degraded-mode behavior.

## Phase 10: geographic intelligence

- Replace the static SVG map with an interactive geographic engine.
- Add clustering, zoom, region filters, bounding-box queries, time windows, heat maps, and layer controls.
- Add country and regional dossiers with risk summaries and dependencies.

## Phase 11: forecasting and decision intelligence

- Persist forecasts, probabilities, confidence bands, trigger maps, falsifiers, outcomes, and calibration scores.
- Add Forecast Portfolio and Hall of Record views.
- Back AURORA GRID, K-ALIGN, CRF, IPR, BLACKGLASS, and AAIK outputs with stored computation rather than labels alone.
- Add red-team review and immutable audit history.

## Phase 12: production quality

- Public deployment with HTTPS, backups, observability, source SLOs, accessibility, mobile performance, pagination, caching, and exports.
- Run load, failure, recovery, and security tests under concurrent collection and analyst use.
- Document deployment, rollback, incident response, and data-retention procedures.

## 9/10 release gate

AURORA is not labeled 9/10 until all of the following are true:

1. At least 25 reliable live source adapters spanning eight or more capability classes.
2. Interactive map with layer controls and sub-second local filtering.
3. Provenance, source health, reliability, and freshness visible for every incident.
4. Working alert, case, forecast, audit, evidence, and red-team workflows.
5. No unlabeled fixture or demonstration data in live mode.
6. Automated tests across Python 3.10-3.13, PostgreSQL, containers, API, dashboard, delivery, and recovery paths.
7. Documented public deployment and rollback.
8. Formal comparison against World Monitor using a published capability matrix.

## Immediate work

Phase 8 begins on `agent/aurora-phase-8-operational-depth`. The first increment adds a source-health API contract and operational telemetry that the analyst console can consume.