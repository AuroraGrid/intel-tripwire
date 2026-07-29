# AURORA LIVE — Canonical Product Specification

## Mission

AURORA LIVE is a free global evidence and decision-intelligence operating system. It combines live events, public media, transportation, disasters, conflict, infrastructure, markets, prediction markets and verified analysis in one system.

The product is not merely a map. It must answer:

- What happened?
- What is directly verified?
- What is plausible but unproven?
- Which reports derive from the same original source?
- What changed?
- What could falsify the assessment?
- What action should follow?

## Canonical workflow

`ROUTER -> SCOUT -> SOURCEGRID -> K-ALIGN -> IPR -> BLACKGLASS-I -> CRF -> COMMAND -> BLACKGLASS-II -> RECORD LOCK`

AAIK governs evidence, instability and exposure across the full workflow. Luna, Terra and Sol operate as the cognitive control plane. AURORA GRID names the complete operating system rather than an internal pipeline stage.

## Primary interfaces

1. **Global Operating Picture** — worldwide events, webcams, markets, aviation, maritime activity, weather, disasters, infrastructure, cyber events and news.
2. **Incident Room** — claim, evidence chain, source quality, confidence, contradictions, alternatives, falsifier, impact and recommended action.
3. **Source Health** — feed health, delay, degradation, fallback and outage state.

## Geographic webcam requirement

The operating picture must support at least 10 curated, health-checked public webcams in each region:

- Oceania
- Africa
- Asia
- Middle East
- Europe
- North America
- South America

A listed camera is not considered live merely because an embed URL exists. Availability, timestamp freshness, geographic identity and source attribution must be monitored.

## Required product domains

The canonical capability registry in `phase32_product_spec.py` is the machine-readable source of truth. The filename is retained for compatibility, while its manifest reflects the current Phase 38 state. It covers live events, replay, evidence, corrections, forecasts, webcams, live imagery, satellite layers, aviation, maritime traffic, disasters, severe weather, internet and power outages, BGP, cyber incidents, infrastructure, energy, commodities, currencies, crypto, global equities, prediction markets, elections, political risk, economic indicators, sanctions, government alerts, social and Telegram intake, watchlists, geofences, alerts, workspaces, reports, PWA access and free public deployment.

Phase 38 provides meaningful transportation implementation through provider adapters, durable run telemetry, freshness-aware qualification, health APIs, a supervised worker and PostgreSQL support. Aviation and maritime remain `PARTIAL`, not `LIVE`, until production persistence, coverage, freshness, licensing and completeness requirements are demonstrated.

## Status semantics

- `LIVE`: implemented and represented by working, qualified code or an operational adapter with current evidence.
- `PARTIAL`: meaningful functionality exists, but the canonical requirement or production qualification is incomplete.
- `PLANNED`: not yet implemented as a qualified product capability.
- `BLOCKED`: implementation is prevented by an explicit external or architectural constraint.
- `NOT_VERIFIED`: a claim exists but lacks sufficient evidence to mark implemented.

No capability may be marked `LIVE` solely because a placeholder, mock, fixture, UI label, planned endpoint, provider registration or ephemeral test exists.

## Public product APIs

- `GET /.well-known/aurora-product.json`
- `GET /api/public/product/capabilities`
- `GET /api/public/product/gaps`
- `GET /api/public/product/gaps?priority=P0`
- `GET /api/public/transport/coverage`
- `GET /api/public/transport/health`
- `GET /api/public/transport/providers`
- `GET /api/public/transport/runs`
- `GET /api/public/transport/workers`
- `GET /api/public/transport/observations`
- `GET /api/public/transport/configuration`
- `GET /api/public/global-operating-picture`

These endpoints expose the product contract and prevent the roadmap from drifting away from the original operating concept.

## Immediate P0 execution order

1. Regional webcam registry and health monitor.
2. Production qualification and coverage expansion for aviation and maritime adapters.
3. Severe-weather, internet-outage and BGP layers.
4. Global equities, energy and prediction-market surfaces.
5. Public PWA shell and no-paywall deployment controls.
6. Unified replay across event, sensor, market and media records.

## Qualification boundary

AURORA LIVE may aggregate third-party public data, but it must preserve provenance, licensing constraints, source health and uncertainty. It must not imply that a feed is complete, real-time or independently verified when those conditions have not been demonstrated.

Provider registration is not live evidence. Transportation qualifies as operational only when a recent successful provider run produces valid observations, persists them durably, remains inside the freshness ceiling, discloses provider and coverage limitations, and satisfies licensing requirements. Aviation-weather observations do not establish complete aircraft-position coverage. AIS reception does not establish complete global vessel coverage.
