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

SCOUT → SOURCEGRID → K-ALIGN → BLACKGLASS → CRF/IPR → COMMAND → AURORA GRID → RECORD LOCK

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

The canonical capability registry in `phase32_product_spec.py` is the machine-readable source of truth. It covers live events, replay, evidence, corrections, forecasts, webcams, live imagery, satellite layers, aviation, maritime traffic, disasters, severe weather, internet and power outages, BGP, cyber incidents, infrastructure, energy, commodities, currencies, crypto, global equities, prediction markets, elections, political risk, economic indicators, sanctions, government alerts, social and Telegram intake, watchlists, geofences, alerts, workspaces, reports, PWA access and free public deployment.

## Status semantics

- `LIVE`: implemented and represented by working code or a qualified adapter.
- `PARTIAL`: meaningful functionality exists, but the canonical requirement is incomplete.
- `PLANNED`: not yet implemented as a qualified product capability.
- `BLOCKED`: implementation is prevented by an explicit external or architectural constraint.
- `NOT_VERIFIED`: a claim exists but lacks sufficient evidence to mark implemented.

No capability may be marked `LIVE` solely because a placeholder, mock, fixture, UI label or planned endpoint exists.

## Public product APIs

- `GET /.well-known/aurora-product.json`
- `GET /api/public/product/capabilities`
- `GET /api/public/product/gaps`
- `GET /api/public/product/gaps?priority=P0`

These endpoints expose the product contract and prevent the roadmap from drifting away from the original operating concept.

## Immediate P0 execution order

1. Regional webcam registry and health monitor.
2. Live aviation and maritime adapters.
3. Severe-weather, internet-outage and BGP layers.
4. Global equities, energy and prediction-market surfaces.
5. Public PWA shell and no-paywall deployment controls.
6. Unified replay across event, sensor, market and media records.

## Qualification boundary

AURORA LIVE may aggregate third-party public data, but it must preserve provenance, licensing constraints, source health and uncertainty. It must not imply that a feed is complete, real-time or independently verified when those conditions have not been demonstrated.
