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

**Release entrypoint:** `aurora-live/release_wsgi.py` currently serves **Phase 44** (`Phase44Application`).

### Completed platform layers

| Phase | Capability |
|------:|------------|
| 38 | Aviation / maritime transport adapters, health, workers |
| 39 | Infrastructure-risk layers (weather, wildfire, outage, BGP, power, cyber, sanctions, government alerts) |
| 40 | Markets: equities/indexes, energy, commodities, FX, crypto, economic indicators, prediction markets |
| 41 | Unified multi-domain replay + media lineage / hash verification |
| 42 | Public Global Operating Picture, Incident Room, Source Health UI |
| 43 | PWA shell, public no-paywall controls, caching headers, abuse rate limits, notification scaffolding |
| 44 | Ops history / redundancy samples + competitive benchmark harness (never auto-claims 10/10) |

Transport and infrastructure remain **PARTIAL** until production persistence, coverage, licensing, and freshness gates pass. Market layers become LIVE only with qualified durable provider runs. Webcams remain non-LIVE until the 70-camera matrix is stream-verified ONLINE in all seven regions.

### Webcam seed tooling (issue #57)

```bash
cd aurora-live
python scripts/seed_webcams.py --database var/webcams.sqlite3
python scripts/verify_webcam_matrix.py --database var/webcams.sqlite3
# optional live probes:
python scripts/verify_webcam_matrix.py --database var/webcams.sqlite3 --probe
```

The seed manifest registers 70 region-balanced placeholders with explicit license notes. **Registration is not ONLINE evidence.**

See:

- `aurora-live/docs/AURORA_LIVE_CANONICAL_PRODUCT_SPEC.md`
- `aurora-live/docs/PHASE40_MARKETS.md`
- `aurora-live/docs/PHASE41_REPLAY_MEDIA.md`
- `aurora-live/docs/PHASE42_PRODUCTION_UI.md`
- `aurora-live/docs/PHASE43_PWA_PUBLIC.md`
- `aurora-live/docs/PHASE44_OPS_BENCHMARK.md`
- `aurora-live/docs/WEBCAM_SEED_MATRIX.md`
- `aurora-live/phase32_product_spec.py`
- `aurora-live/phase44_complete.py`

## Evidence discipline

No capability is marked live solely because a placeholder, mock, fixture, UI label, planned endpoint, provider registration, or ephemeral test exists. Public-data integrations must preserve provenance, licensing constraints, source health, freshness, completeness limits, and uncertainty.

## Contact

- Hasan Raza Kazmi
- Email: Kazmihasan624@gmail.com 
- Location: Washington DC
- Work preference: fully remote
