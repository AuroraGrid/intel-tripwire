# AURORA LIVE / Intel Tripwire

A global evidence and decision-intelligence system designed to combine live events, public media, transportation, disasters, infrastructure, markets, forecasting signals, and verified analysis in one operating picture.

## Canonical links

- Repository: https://github.com/hr185882-creator/intel-tripwire
- GitHub profile: https://github.com/hr185882-creator
- AURORA GRID live site: https://hr185882-creator.github.io/aurora-grid-grindwire-site/
- AURORA GRID v2 specification: https://github.com/hr185882-creator/aurora-grid-grindwire-site/blob/main/docs/AURORA_GRID_V2_CANONICAL.md
- AURORA Learning Platform: https://github.com/hr185882-creator/aurora-learning-platform
- Inflection Point Research: https://github.com/hr185882-creator/inflection-point-research

## Mission

AURORA LIVE is intended to answer:

- What happened?
- What is directly verified?
- What is plausible but unproven?
- Which reports derive from the same source?
- What changed?
- What could falsify the assessment?
- What action should follow?

## Canonical workflow

`ROUTER -> SCOUT -> SOURCEGRID -> K-ALIGN -> IPR -> BLACKGLASS-I -> CRF -> COMMAND -> BLACKGLASS-II -> RECORD LOCK`

AAIK operates across the complete workflow. Luna expands possibilities, Terra validates the operating picture, and Sol resolves priorities and action. AURORA GRID names the complete operating system rather than an internal pipeline stage.

## Primary interfaces

1. **Global Operating Picture** - worldwide events, media, transport, weather, disasters, infrastructure, cyber events, and markets.
2. **Incident Room** - claims, evidence chains, source quality, contradictions, alternatives, falsifiers, impact, and action.
3. **Source Health** - feed availability, delay, degradation, fallback, and outage state.

## Current implementation status

The repository contains a staged Python application and a machine-readable capability registry. The product specification distinguishes implemented, partial, planned, blocked, and unverified capabilities so roadmap language is not mistaken for working functionality.

Phase 38 adds transport-provider adapters, durable provider-run telemetry, freshness-aware qualification, a supervised worker, health APIs, PostgreSQL support, and deployment configuration. This is meaningful aviation and maritime implementation, but it does not establish complete global aircraft-position or vessel coverage. The capability registry therefore classifies both transportation domains as PARTIAL pending qualified production persistence, coverage validation, and licensing review.

Current documented priorities include:

- regional webcam registry and health monitoring
- completion and production qualification of aviation and maritime coverage
- severe-weather, internet-outage, and BGP layers
- equities, energy, and prediction-market surfaces
- public PWA deployment
- unified event and evidence replay

See:

- `aurora-live/docs/AURORA_LIVE_CANONICAL_PRODUCT_SPEC.md`
- `aurora-live/phase32_product_spec.py`
- `aurora-live/phase38_complete.py`
- `aurora-live/docs/PHASE38_PROVIDER_ADAPTERS.md`

## Evidence discipline

No capability is marked live solely because a placeholder, mock, fixture, UI label, planned endpoint, provider registration, or ephemeral test exists. Public-data integrations must preserve provenance, licensing constraints, source health, freshness, completeness limits, and uncertainty.

## Contact

- Hasan Raza Kazmi
- Email: Grindwireproject@gmail.com
- Location: Sargodha, Pakistan
- Work preference: fully remote
